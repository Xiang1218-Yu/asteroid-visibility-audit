#!/usr/bin/env python3
"""Starter for a fixed-sky-target visibility report."""
import argparse,json,sys
from pathlib import Path
class Reject(ValueError):pass
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--ledger',required=True);a=p.parse_args(argv)
 try:
  d=json.loads(Path(a.input).read_text());o=d['observer'];t=d['target'];w=d['window'];l=d['limits'];h=90-abs(o['latitude_deg']-t['dec_deg']);ok=h>=l['min_altitude_deg'];v={'observer_id':o['id'],'status':'OBSERVABLE' if ok else 'NOT_VISIBLE','sun_separation_deg':180.0,'sample_times_s':[w['start_s'],w['end_s']],'windows':([{'start_s':w['start_s'],'end_s':w['end_s'],'duration_s':w['end_s']-w['start_s'],'peak_altitude_deg':h}] if ok else [])};Path(a.output).write_text(json.dumps(v));Path(a.ledger).write_text(json.dumps({'status':v['status']})+'\n');return 0
 except Exception:print('REJECT INVALID_INPUT',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
