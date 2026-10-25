// mk20deck-scroll — the MK20 deck's K1 scroll-back for Codex Desktop (macOS).
//
// The only process of the deck that needs macOS Accessibility: it posts scroll-wheel events into the Codex window.
// The Python daemon never does (it only runs this helper).  Built and ad-hoc signed by `mk20deck scroll-setup`:
//
//   clang -fobjc-arc -O2 -Wall -mmacosx-version-min=12.0 -framework AppKit -framework ApplicationServices \
//         -o mk20deck-scroll mk20deck-scroll.m
//   codesign --force --sign - --identifier com.leo.mk20deck-scroll mk20deck-scroll
//
// Usage:
//   mk20deck-scroll --check                 "trusted: yes|no" (AXIsProcessTrusted) and "responsible: self|parent"
//                                           (whom TCC judges); exit 0 = trusted, 3 = not
//   mk20deck-scroll --request               same, but asks macOS to show its Accessibility dialog (adds the helper to
//                                           系统设置 → 隐私与安全性 → 辅助功能, switched off until the user allows it)
//   mk20deck-scroll --app ID --lines N      scroll the app's frontmost on-screen window: N > 0 = older (up),
//                                           N < 0 = newer (down), in wheel lines
//   mk20deck-scroll --app ID --bottom       scroll far down (back to the latest content)
//     ID: bundle id (com.openai.codex) or the app's name / executable name
//     --via pid   (default) CGEventPostToPid: straight into the app, the mouse cursor never moves
//     --via hid   CGEventPost at the HID tap with the event location set inside the window (cursor not warped)
//     --at FX,FY  point inside the window as fractions of its size (default 0.6,0.45: the conversation, above the
//                 composer and right of the sidebar)
//     --dry-run   find the window and print the point, post nothing
//   Exit: 0 ok, 2 usage, 3 not trusted, 4 app not running / no on-screen window, 5 event not created / posted.
//
// Never synthesizes key presses, never moves the mouse, never activates or raises a window.
//
// TCC attributes a spawned process to its "responsible" ancestor (the Terminal, or the Python daemon), so the helper
// first re-spawns itself with responsibility disclaimed (the same private libSystem call Chromium / LLDB use): the
// Accessibility permission is then checked for, and granted to, this binary alone.
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define VERSION "1"
#define EXIT_USAGE 2
#define EXIT_UNTRUSTED 3
#define EXIT_NO_WINDOW 4
#define EXIT_EVENT 5
#define CHILD_ENV "MK20DECK_SCROLL_DISCLAIMED"
#define BOTTOM_EVENTS 6
#define BOTTOM_LINES 2000
#define MAX_LINES 1000

extern char **environ;
typedef int (*disclaim_fn)(posix_spawnattr_t *, int);
typedef pid_t (*responsible_fn)(pid_t);

static int usage(void) {
    fprintf(stderr, "usage: mk20deck-scroll --check | --request | --version\n"
                    "       mk20deck-scroll --app ID (--lines N | --bottom) [--via pid|hid] [--at FX,FY] [--dry-run]\n");
    return EXIT_USAGE;
}

// Re-run this binary as its own responsible process.  Returns its exit status, or -1 when that is not possible
// (the caller then continues in-process; the permission is checked for the parent instead).
static int run_disclaimed(char **argv) {
    disclaim_fn disclaim = (disclaim_fn)dlsym(RTLD_DEFAULT, "responsibility_spawnattrs_setdisclaim");
    if (disclaim == NULL) return -1;
    char path[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0) return -1;
    posix_spawnattr_t attr;
    if (posix_spawnattr_init(&attr) != 0) return -1;
    if (disclaim(&attr, 1) != 0) {
        posix_spawnattr_destroy(&attr);
        return -1;
    }
    setenv(CHILD_ENV, "1", 1);
    pid_t pid;
    int rc = posix_spawn(&pid, path, NULL, &attr, argv, environ);
    posix_spawnattr_destroy(&attr);
    unsetenv(CHILD_ENV);
    if (rc != 0) return -1;
    int st = 0;
    while (waitpid(pid, &st, 0) < 0) {
        if (errno != EINTR) return EXIT_EVENT;
    }
    if (WIFEXITED(st)) return WEXITSTATUS(st);
    return 128 + (WIFSIGNALED(st) ? WTERMSIG(st) : 0);
}

static int report_trust(BOOL prompt) {
    BOOL ok;
    if (prompt) {
        NSDictionary *opts = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
        ok = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)opts);
    } else {
        ok = AXIsProcessTrusted();
    }
    printf("trusted: %s\n", ok ? "yes" : "no");
    // "self": TCC judges this binary (the disclaimed re-spawn worked); "parent": the Terminal / Python that ran it
    responsible_fn who = (responsible_fn)dlsym(RTLD_DEFAULT, "responsibility_get_pid_responsible_for_pid");
    if (who != NULL) printf("responsible: %s\n", who(getpid()) == getpid() ? "self" : "parent");
    return ok ? 0 : EXIT_UNTRUSTED;
}

static pid_t find_app(const char *ident) {
    NSString *want = [NSString stringWithUTF8String:ident];
    for (NSRunningApplication *app in [NSRunningApplication runningApplicationsWithBundleIdentifier:want]) {
        if (!app.terminated && app.activationPolicy == NSApplicationActivationPolicyRegular) return app.processIdentifier;
    }
    for (NSRunningApplication *app in [[NSWorkspace sharedWorkspace] runningApplications]) {
        if (app.terminated || app.activationPolicy != NSApplicationActivationPolicyRegular) continue;
        if ([app.localizedName isEqualToString:want] || [app.executableURL.lastPathComponent isEqualToString:want])
            return app.processIdentifier;
    }
    return 0;
}

// The app's frontmost normal window that is on screen now (front-to-back order of the window list).
static BOOL find_window(pid_t pid, CGRect *bounds, CGWindowID *wid) {
    CFArrayRef list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
                                                 kCGNullWindowID);
    if (list == NULL) return NO;
    BOOL found = NO;
    for (CFIndex i = 0; i < CFArrayGetCount(list) && !found; i++) {
        NSDictionary *w = (__bridge NSDictionary *)CFArrayGetValueAtIndex(list, i);
        if ([w[(__bridge NSString *)kCGWindowOwnerPID] intValue] != pid) continue;
        if ([w[(__bridge NSString *)kCGWindowLayer] intValue] != 0) continue;
        NSNumber *alpha = w[(__bridge NSString *)kCGWindowAlpha];
        if (alpha != nil && [alpha doubleValue] <= 0.0) continue;
        CGRect r;
        if (!CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)w[(__bridge NSString *)kCGWindowBounds], &r))
            continue;
        if (r.size.width < 200 || r.size.height < 150) continue;
        *bounds = r;
        *wid = (CGWindowID)[w[(__bridge NSString *)kCGWindowNumber] unsignedIntValue];
        found = YES;
    }
    CFRelease(list);
    return found;
}

static BOOL post_scroll(pid_t pid, CGWindowID wid, CGPoint pt, int32_t lines, BOOL via_pid) {
    CGEventRef ev = CGEventCreateScrollWheelEvent(NULL, kCGScrollEventUnitLine, 1, lines);
    if (ev == NULL) return NO;
    CGEventSetLocation(ev, pt);
    // AppKit routes a mouse-type event to the window these name (needed when it is not delivered by the window server)
    CGEventSetIntegerValueField(ev, kCGMouseEventWindowUnderMousePointer, wid);
    CGEventSetIntegerValueField(ev, kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, wid);
    if (via_pid) {
        CGEventPostToPid(pid, ev);
    } else {
        CGEventPost(kCGHIDEventTap, ev);
    }
    CFRelease(ev);
    return YES;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (getenv(CHILD_ENV) == NULL) {
            int rc = run_disclaimed(argv);
            if (rc >= 0) return rc;
            fprintf(stderr, "mk20deck-scroll: cannot run as its own responsible process; "
                            "the permission is checked for the parent process\n");
        }
        const char *app = NULL, *via = "pid";
        long lines = 0;
        BOOL have_lines = NO, bottom = NO, check = NO, request = NO, dry = NO;
        double fx = 0.6, fy = 0.45;
        for (int i = 1; i < argc; i++) {
            const char *a = argv[i];
            if (strcmp(a, "--check") == 0) {
                check = YES;
            } else if (strcmp(a, "--request") == 0) {
                request = YES;
            } else if (strcmp(a, "--version") == 0) {
                printf("mk20deck-scroll %s\n", VERSION);
                return 0;
            } else if (strcmp(a, "--bottom") == 0) {
                bottom = YES;
            } else if (strcmp(a, "--dry-run") == 0) {
                dry = YES;
            } else if (strcmp(a, "--app") == 0 && i + 1 < argc) {
                app = argv[++i];
            } else if (strcmp(a, "--via") == 0 && i + 1 < argc) {
                via = argv[++i];
            } else if (strcmp(a, "--lines") == 0 && i + 1 < argc) {
                char *end = NULL;
                errno = 0;
                lines = strtol(argv[++i], &end, 10);
                if (errno != 0 || end == NULL || *end != '\0' || lines == 0 || labs(lines) > MAX_LINES) return usage();
                have_lines = YES;
            } else if (strcmp(a, "--at") == 0 && i + 1 < argc) {
                char *end = NULL;
                fx = strtod(argv[++i], &end);  // "FX,FY" exactly: no trailing junk (0.5,0.5x is a usage error)
                if (end == NULL || *end != ',') return usage();
                fy = strtod(end + 1, &end);
                if (end == NULL || *end != '\0' || fx < 0 || fx > 1 || fy < 0 || fy > 1) return usage();
            } else {
                return usage();
            }
        }
        if (check || request) return report_trust(request);
        if (app == NULL || have_lines == bottom || (strcmp(via, "pid") != 0 && strcmp(via, "hid") != 0)) return usage();
        if (!dry && !AXIsProcessTrusted()) {
            fprintf(stderr, "mk20deck-scroll: not trusted for Accessibility (run: mk20deck scroll-setup)\n");
            return EXIT_UNTRUSTED;
        }
        pid_t pid = find_app(app);
        if (pid <= 0) {
            fprintf(stderr, "mk20deck-scroll: %s is not running\n", app);
            return EXIT_NO_WINDOW;
        }
        CGRect r;
        CGWindowID wid = 0;
        if (!find_window(pid, &r, &wid)) {
            fprintf(stderr, "mk20deck-scroll: %s has no window on screen\n", app);
            return EXIT_NO_WINDOW;
        }
        CGPoint pt = CGPointMake(r.origin.x + r.size.width * fx, r.origin.y + r.size.height * fy);
        if (dry) {
            printf("pid %d window %u at %.0f,%.0f\n", (int)pid, (unsigned)wid, pt.x, pt.y);
            return 0;
        }
        BOOL via_pid = strcmp(via, "pid") == 0;
        if (!bottom) return post_scroll(pid, wid, pt, (int32_t)lines, via_pid) ? 0 : EXIT_EVENT;
        for (int i = 0; i < BOTTOM_EVENTS; i++) {  // a few large wheel-downs: the view stops at the newest content
            if (!post_scroll(pid, wid, pt, -BOTTOM_LINES, via_pid)) return EXIT_EVENT;
            usleep(15000);
        }
        return 0;
    }
}
