#!/usr/bin/env python3
"""ONVIF PTZ helper for the MK20 camera (pan/tilt only; no zoom/focus hardware).
Usage: ptz.py status | presets | setpreset NAME | goto TOKEN | rel DX DY | abs X Y
Presets: 1=orig-20261027 (-0.074788,-0.638494), 2=mk20 (-0.054742,-0.638494).
Note: RelativeMove rejects very small steps (0.004 failed, 0.02 ok; 0.02 pan ~= 125 px at 2560 wide).
"""
import os,sys,base64,hashlib,datetime,urllib.request,re
pw=os.environ.get('MK20_CAM_PASS') or open(os.path.expanduser('~/.config/mk20-cam/cred')).read()
ip=os.environ.get('MK20_CAM_IP','192.168.88.125')
def call(user,body,path='/onvif/device_service'):
  n=os.urandom(16);c=datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
  dg=base64.b64encode(hashlib.sha1(n+c.encode()+pw.encode()).digest()).decode()
  env=f'''<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header><Security xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken><Username>{user}</Username><Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{dg}</Password><Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(n).decode()}</Nonce><Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{c}</Created></UsernameToken></Security></s:Header><s:Body>{body}</s:Body></s:Envelope>'''
  r=urllib.request.Request(f'http://{ip}:2020{path}',env.encode(),{'Content-Type':'application/soap+xml'})
  try: return urllib.request.urlopen(r,timeout=8).read().decode()
  except urllib.error.HTTPError as e: return 'ERR%d %s'%(e.code,e.read().decode()[:300])
import re,time
S='/onvif/service';NS='http://www.onvif.org/ver20/ptz/wsdl'
def b(x): return x.split('Body>')[1][:400] if 'Body>' in x else x[:400]
def status():
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<GetStatus xmlns="{NS}"><ProfileToken>profile_1</ProfileToken></GetStatus>',S)
  return re.findall(r'PanTilt x="([^"]+)" y="([^"]+)"',r), re.findall(r'MoveStatus><tt:PanTilt>(\w+)',r)
cmd=sys.argv[1]
if cmd=='status': print(status())
elif cmd=='setpreset':
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<SetPreset xmlns="{NS}"><ProfileToken>profile_1</ProfileToken><PresetName>{sys.argv[2]}</PresetName></SetPreset>',S); print('set',re.findall(r'PresetToken>([^<]*)',r) or b(r))
elif cmd=='presets':
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<GetPresets xmlns="{NS}"><ProfileToken>profile_1</ProfileToken></GetPresets>',S)
  print(re.findall(r'Preset token="([^"]*)"><tt:Name>([^<]*)</tt:Name>.*?PanTilt[^>]*x="([^"]+)" y="([^"]+)"',r) or b(r))
elif cmd=='rel':
  x,y=sys.argv[2],sys.argv[3]
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<RelativeMove xmlns="{NS}"><ProfileToken>profile_1</ProfileToken><Translation><PanTilt xmlns="http://www.onvif.org/ver10/schema" x="{x}" y="{y}" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace"/></Translation></RelativeMove>',S)
  print('rel', 'ERR' if r.startswith('ERR') else 'ok'); 
  for i in range(20):
    time.sleep(0.5); s=status()
    if s[1]==['idle'] and i>1: break
  print(s)
elif cmd=='abs':
  x,y=sys.argv[2],sys.argv[3]
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<AbsoluteMove xmlns="{NS}"><ProfileToken>profile_1</ProfileToken><Position><PanTilt xmlns="http://www.onvif.org/ver10/schema" x="{x}" y="{y}"/></Position></AbsoluteMove>',S)
  print('abs','ERR' if r.startswith('ERR') else 'ok'); time.sleep(4); print(status())
elif cmd=='goto':
  r=call(os.environ.get('MK20_CAM_USER','admin'),f'<GotoPreset xmlns="{NS}"><ProfileToken>profile_1</ProfileToken><PresetToken>{sys.argv[2]}</PresetToken></GotoPreset>',S); print('goto','ERR' if r.startswith('ERR') else 'ok'); time.sleep(4); print(status())
