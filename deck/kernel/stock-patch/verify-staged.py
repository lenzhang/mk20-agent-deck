import sys,struct,hashlib,zlib
stock=open(sys.argv[1],'rb').read(); new=open(sys.argv[2],'rb').read()
H=lambda b:hashlib.sha256(b).hexdigest()
print('new sha',H(new),len(new))
def hdr(b):
    assert b[:8]==b'ANDROID!'
    ks,ka,rs,ra,ss,sa,ta,ps,hv=struct.unpack('<9I',b[8:44]); return dict(ks=ks,ka=ka,rs=rs,ra=ra,ss=ss,sa=sa,ta=ta,ps=ps,hv=hv)
hs,hn=hdr(stock),hdr(new); print('stock hdr',hs); print('new   hdr',hn)
print('header page identical:',stock[:2048]==new[:2048])
ps=hs['ps']; k=stock[ps:ps+hs['ks']]; kn=new[ps:ps+hn['ks']]
print('kernel size equal',len(k)==len(kn))
def piggy(z):
    i=z.find(b'\x1f\x8b\x08'); d=zlib.decompressobj(31); img=d.decompress(z[i:]); 
    return i,img,len(z[i:])-len(d.unused_data)
i1,img1,l1=piggy(k); i2,img2,l2=piggy(kn)
print('piggy off',i1,i2,'len',l1,l2,'Image len',len(img1),len(img2))
print('decompressor prefix identical', k[:i1]==kn[:i2])
print('zImage tail identical', k[i1+l1:]==kn[i2+l2:])
diff=[(o,img1[o],img2[o]) for o in range(len(img1)) if img1[o]!=img2[o]]
print('Image diffs:',[(hex(o),hex(a),hex(b)) for o,a,b in diff])
# rest of image after kernel pages
end=ps+((hn['ks']+ps-1)//ps)*ps
print('new image ends at',end,'len',len(new))
zd=[o for o in range(len(new)) if new[o]!=stock[o]]
print('outer-byte diffs in kernel region only:', all(ps<=o<ps+hn['ks'] for o in zd), 'count',len(zd))
print('stock prefix sha (same len)',H(stock[:len(new)]))
