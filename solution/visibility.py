#!/usr/bin/env python3
"""Fixed-sky-target topocentric visibility auditor."""
from __future__ import annotations
import argparse, hashlib, json, math, os, sys, tempfile
from pathlib import Path
ROT=360.0/86164.0905; EPS=1e-9
class Reject(ValueError):
 def __init__(self,c):super().__init__(c);self.code=c
def num(v,c):
 if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise Reject(c)
 return float(v)
def obj(v,c='INVALID_STRUCTURE'):
 if not isinstance(v,dict):raise Reject(c)
 return v
def exact(v,keys,c='INVALID_STRUCTURE'):
 if set(v)!=set(keys):raise Reject(c)
def rad(x):return x*math.pi/180
def norm(x):return x%360
def signed(x):return (x+180)%360-180
def sky(v,c):
 v=obj(v);exact(v,['ra_deg','dec_deg']);ra=num(v['ra_deg'],'INVALID_'+c);dc=num(v['dec_deg'],'INVALID_'+c)
 if not(0<=ra<360 and -90<=dc<=90):raise Reject('INVALID_'+c)
 return ra,dc
def parse(raw):
 raw=obj(raw);exact(raw,['observer','target','sun','window','limits'])
 o=obj(raw['observer']);exact(o,['id','latitude_deg','longitude_deg','sidereal_zero_deg'])
 if not isinstance(o['id'],str) or not o['id']:raise Reject('INVALID_OBSERVER')
 lat=num(o['latitude_deg'],'INVALID_OBSERVER');lon=num(o['longitude_deg'],'INVALID_OBSERVER');zero=num(o['sidereal_zero_deg'],'INVALID_OBSERVER')
 if not(-90<=lat<=90 and -180<=lon<=180 and 0<=zero<360):raise Reject('INVALID_OBSERVER')
 tar=sky(raw['target'],'TARGET');sun=sky(raw['sun'],'SUN');win=obj(raw['window']);exact(win,['start_s','end_s','cadence_s'])
 a=num(win['start_s'],'INVALID_WINDOW');b=num(win['end_s'],'INVALID_WINDOW');step=num(win['cadence_s'],'INVALID_WINDOW')
 if a<0 or b<=a or step<=0:raise Reject('INVALID_WINDOW')
 lim=obj(raw['limits']);exact(lim,['min_altitude_deg','sun_avoidance_deg','min_window_s'])
 alt=num(lim['min_altitude_deg'],'INVALID_LIMITS');avoid=num(lim['sun_avoidance_deg'],'INVALID_LIMITS');minimum=num(lim['min_window_s'],'INVALID_LIMITS')
 if not(-90<=alt<=90 and 0<=avoid<=180 and minimum>=0):raise Reject('INVALID_LIMITS')
 return o['id'],lat,lon,zero,tar,sun,a,b,step,alt,avoid,minimum
def sep(a,b):
 c=math.sin(rad(a[1]))*math.sin(rad(b[1]))+math.cos(rad(a[1]))*math.cos(rad(b[1]))*math.cos(rad(signed(a[0]-b[0])))
 return math.degrees(math.acos(max(-1,min(1,c))))
def altitude(lat,lon,zero,t,tar):
 h=signed(norm(zero+lon+ROT*t)-tar[0]);s=math.sin(rad(lat))*math.sin(rad(tar[1]))+math.cos(rad(lat))*math.cos(rad(tar[1]))*math.cos(rad(h))
 return math.degrees(math.asin(max(-1,min(1,s))))
def times(a,b,step):
 out=[a];n=1
 while a+n*step<b-EPS:out.append(a+n*step);n+=1
 if abs(out[-1]-b)>EPS:out.append(b)
 return out
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def assess(raw):
 ident,lat,lon,zero,tar,sun,a,b,step,cut,avoid,minimum=parse(raw);solar=sep(tar,sun);samples=[]
 for t in times(a,b,step):
  h=altitude(lat,lon,zero,t,tar);samples.append((round(t,6),round(h,6),h+EPS>=cut and solar+EPS>=avoid))
 windows=[];active=None
 for t,h,ok in samples:
  if ok:
   active={'start_s':t,'end_s':t,'peak_altitude_deg':h} if active is None else {**active,'end_s':t,'peak_altitude_deg':max(active['peak_altitude_deg'],h)}
  elif active is not None:
   active['duration_s']=round(active['end_s']-active['start_s'],6);windows.append(active);active=None
 if active is not None:active['duration_s']=round(active['end_s']-active['start_s'],6);windows.append(active)
 longest=max((v['duration_s'] for v in windows),default=0);status='NOT_VISIBLE' if not windows else ('OBSERVABLE' if longest+EPS>=minimum else 'BRIEF')
 return {'observer_id':ident,'status':status,'sun_separation_deg':round(solar,6),'sample_times_s':[x[0] for x in samples],'windows':windows}
def ledger(p):
 if not p.exists():return []
 try:
  events=[json.loads(line) for line in p.read_text(encoding='utf-8').splitlines()]
  if any(set(v)!={'fingerprint','observer_id','status'} or not all(isinstance(x,str) for x in v.values()) for v in events):raise ValueError
  return events
 except Exception as e:raise Reject('LEDGER_READ') from e
def stage(dst,data):
 dst.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(prefix='.visibility-',suffix='.tmp',dir=dst.parent);tmp=Path(name)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  return tmp
 except Exception:tmp.unlink(missing_ok=True);raise
def commit(report,log,value,fingerprint):
 events=ledger(log)
 if fingerprint not in {x['fingerprint'] for x in events}:events.append({'fingerprint':fingerprint,'observer_id':value['observer_id'],'status':value['status']})
 pending=[]
 try:
  pending=[(stage(report,(canonical(value)+'\n').encode()),report),(stage(log,(''.join(canonical(x)+'\n' for x in events)).encode()),log)]
  for tmp,dst in pending:os.replace(tmp,dst)
 finally:
  for tmp,_ in pending:tmp.unlink(missing_ok=True)
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--ledger',required=True);a=p.parse_args(argv)
 try:
  raw=json.loads(Path(a.input).read_text(encoding='utf-8'));value=assess(raw);commit(Path(a.output),Path(a.ledger),value,hashlib.sha256(canonical(raw).encode()).hexdigest());return 0
 except (OSError,json.JSONDecodeError,Reject,ValueError) as e:print('REJECT '+(e.code if isinstance(e,Reject) else 'INPUT_READ'),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
