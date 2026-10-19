# Conservative reader for a bounded, flat, non-sensitive agent.status JSON object.
# Unsupported JSON (nested values, unicode escapes, duplicate keys) yields no fields.
# Only identifier-valued metadata is exported: URLs/free text cannot reach health.
function ws() { sub(/^[ \t\r\n]+/, "", s) }
function token(    t) {
  ws()
  if (match(s, /^"([^"\\[:cntrl:]]|\\["\\\/bfnrt])*"/)) {
    t=substr(s,1,RLENGTH); s=substr(s,RLENGTH+1); return t
  }
  if (match(s, /^(true|false|null|-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?)/)) {
    t=substr(s,1,RLENGTH); s=substr(s,RLENGTH+1); return t
  }
  bad=1; return ""
}
{ s=s $0 "\n"; if (length(s)>4096) { bad=1; exit } }
END {
  if (bad) exit
  ws(); if (substr(s,1,1)!="{") exit; s=substr(s,2); ws()
  if (substr(s,1,1)!="}") {
    while (1) {
      k=token(); if (bad || k !~ /^"[^"\\]*"$/ || seen[k]++) exit
      ws(); if (substr(s,1,1)!=":") exit; s=substr(s,2)
      v=token(); if (bad) exit
      if (k=="\"allowed\"" && (v=="true" || v=="false")) out=out "allowed=" v "\n"
      if (k=="\"state\"" || k=="\"provider\"" || k=="\"route\"" || k=="\"last_error_class\"") {
        if (v ~ /^"[A-Za-z0-9_.-]+"$/ && length(v)<=82) out=out substr(k,2,length(k)-2) "=" substr(v,2,length(v)-2) "\n"
      }
      ws(); c=substr(s,1,1); if (c=="}") break
      if (c!=",") exit; s=substr(s,2)
    }
  }
  s=substr(s,2); ws(); if (s!="") exit
  printf "%s",out
}
