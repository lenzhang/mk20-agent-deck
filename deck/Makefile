test:
	shellcheck -s sh -S warning firmware/rescue/*.sh firmware/release/sbin/mk20d firmware/release/share/*.sh tools/mk20-release-build.sh tools/mk20-install.sh tools/mk20-rescue-update.sh
	MK20_TEST_BUSYBOX=$${MK20_TEST_BUSYBOX:-$$( [ -x /workspace/tools/bb127/busybox ] && echo /workspace/tools/bb127/busybox )} uv run --with pytest python -m pytest -q tests

release:
	sh tools/mk20-release-build.sh firmware out $$(git rev-parse --short HEAD 2>/dev/null || echo dev)

.PHONY: test release
