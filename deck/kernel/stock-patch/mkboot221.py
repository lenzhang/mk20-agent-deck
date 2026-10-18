#!/usr/bin/env python3
"""Binary-patch the STOCK MK20 boot image (#221) in place: ep3 ISO->INT (+ optional ep5out INT-capable).
Only the gzip piggy changes; decompressor/tables/header byte-identical. Verifies everything."""
import zlib, struct, hashlib, subprocess, sys
SRC, OUT, EP5 = sys.argv[1], sys.argv[2], ('--ep5out-int' in sys.argv)
b = open(SRC, 'rb').read()
assert b[:8] == b'ANDROID!'
ks = struct.unpack('<I', b[8:12])[0]; z = b[2048:2048+ks]
assert struct.unpack_from('<I', z, 0x24)[0] == 0x016f2818
IN0 = z.find(b'\x1f\x8b\x08'); d = zlib.decompressobj(31); img0 = d.decompress(z[IN0:]); IN1 = len(z)-len(d.unused_data)
assert subprocess.run(['gzip','-n','-9','-c'], input=img0, capture_output=True).stdout == z[IN0:IN1], 'round-trip not identical'
img = bytearray(img0); base = 0xC0008000
def ep(name):
    off = img.find(b'\0'+name+b'\0')+1; p = struct.pack('<I', base+off)
    refs=[]; i=img.find(p)
    while i>=0: refs.append(i); i=img.find(p,i+1)
    ss=[r-28 for r in refs if img[r-28+68] in range(0,6) and struct.unpack_from('<I',img,r-28+32)[0]>=base]
    assert len(ss)==1, (name, refs); return ss[0]
s3 = ep(b'ep3-iso'); assert img[s3+68]==3 and img[s3+72]==3 and img[s3+44]==0x32 and img[s3+73]==1
img[s3+44]=0x38; img[s3+73]=0x03
if EP5:
    s5 = ep(b'ep5out-bulk'); assert img[s5+68]==5 and img[s5+72]==5 and img[s5+44]==0x24 and img[s5+73]==2
    img[s5+44]=0x2c
pg = subprocess.run(['gzip','-n','-9','-c'], input=bytes(img), capture_output=True).stdout
delta = (IN1-IN0)-len(pg); assert 0 <= delta <= 64, ('piggy grew', delta)
if delta: pg = pg[:3]+bytes([pg[3]|8])+pg[4:10]+b'k'*(delta-1)+b'\0'+pg[10:]   # asciz FNAME pad; kernel __gunzip skips it
nz = z[:IN0]+pg+z[IN1:]; assert len(nz)==len(z) and nz[:IN0]==z[:IN0] and nz[IN1:]==z[IN1:]
d = zlib.decompressobj(31); out = d.decompress(nz[IN0:IN1]); assert d.eof and not d.unused_data and out == bytes(img)
assert subprocess.run(['gzip','-dc'], input=nz[IN0:IN1], capture_output=True).stdout == bytes(img)
diffs = [(hex(i), img0[i], out[i]) for i in range(len(img0)) if img0[i]!=out[i]]
end = 2048+((ks+2047)//2048)*2048
new = b[:2048]+nz+b[2048+ks:end]
assert len(new)==end and new[:2048]==b[:2048]
open(OUT,'wb').write(new)
print('piggy pad', delta, 'Image diffs', diffs, 'boot-new', len(new), hashlib.sha256(new).hexdigest())
print('prefix of stock partition sha (same length):', hashlib.sha256(b[:end]).hexdigest())
