import hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
PROGRAM=Path(os.environ.get('PROGRAM_DIR','/app'))/'visibility.py'
def scene(**changes):
 x={'observer':{'id':'OBS-7','latitude_deg':0.0,'longitude_deg':0.0,'sidereal_zero_deg':0.0},'target':{'ra_deg':0.0,'dec_deg':0.0},'sun':{'ra_deg':180.0,'dec_deg':0.0},'window':{'start_s':0.0,'end_s':7200.0,'cadence_s':1800.0},'limits':{'min_altitude_deg':20.0,'sun_avoidance_deg':30.0,'min_window_s':1800.0}}
 x.update(changes);return x
def run(payload,report=b'old-report\n',ledger=b'old-ledger\n'):
 with tempfile.TemporaryDirectory() as raw:
  d=Path(raw);i=d/'in.json';o=d/'report.json';l=d/'ledger.jsonl';i.write_text(json.dumps(payload),encoding='utf-8');o.write_bytes(report);l.write_bytes(ledger)
  p=subprocess.run([sys.executable,str(PROGRAM),'--input',str(i),'--output',str(o),'--ledger',str(l)],capture_output=True,text=True)
  return p,o.read_bytes(),l.read_bytes(),sorted(v.name for v in d.iterdir())
def success(payload):
 p,o,_,_=run(payload,b'',b'');assert p.returncode==0,p.stderr;return json.loads(o),o
def rejected(payload):
 p,o,l,files=run(payload);assert p.returncode!=0;assert p.stderr.splitlines()[0].startswith('REJECT ');assert o==b'old-report\n' and l==b'old-ledger\n';assert files==['in.json','ledger.jsonl','report.json']
def test_sidereal_rotation_changes_visibility_within_window():
 """R2：每一采样时刻必须按恒星时重新计算仰角。"""
 d,_=success(scene(window={'start_s':0.0,'end_s':21600.0,'cadence_s':10800.0},limits={'min_altitude_deg':60.0,'sun_avoidance_deg':1.0,'min_window_s':0.0}))
 assert d['sample_times_s']==[0.0,10800.0,21600.0];assert d['status']=='OBSERVABLE';assert [(w['start_s'],w['end_s']) for w in d['windows']]==[(0.0,0.0)]
def test_spherical_altitude_uses_latitude_declination_and_hour_angle():
 """R2：球面三角中的台站纬度、赤纬和小时角不可省略。"""
 d,_=success(scene(observer={'id':'N','latitude_deg':60.0,'longitude_deg':0.0,'sidereal_zero_deg':0.0},target={'ra_deg':0.0,'dec_deg':30.0},window={'start_s':0.0,'end_s':1.0,'cadence_s':5.0},limits={'min_altitude_deg':59.9,'sun_avoidance_deg':1.0,'min_window_s':0.0}))
 assert d['status']=='OBSERVABLE';assert d['windows'][0]['peak_altitude_deg']==60.0
def test_solar_avoidance_blocks_high_altitude_target():
 """R3：高仰角目标也必须服从太阳避让角。"""
 d,_=success(scene(sun={'ra_deg':2.0,'dec_deg':0.0},limits={'min_altitude_deg':-90.0,'sun_avoidance_deg':5.0,'min_window_s':0.0}))
 assert d['sun_separation_deg']==2.0 and d['status']=='NOT_VISIBLE' and d['windows']==[]
def test_equality_at_altitude_and_solar_threshold_is_visible():
 """R2/R3：两个资格门槛的相等边界均为可见。"""
 d,_=success(scene(sun={'ra_deg':30.0,'dec_deg':0.0},limits={'min_altitude_deg':90.0,'sun_avoidance_deg':30.0,'min_window_s':0.0},window={'start_s':0.0,'end_s':1.0,'cadence_s':4.0}))
 assert d['status']=='OBSERVABLE';assert d['windows'][0]['duration_s']==0.0
def test_final_irregular_sample_and_visibility_gap_split_windows():
 """R4：不整除终点必须采样，且不可见样本必须分割窗口。"""
 d,_=success(scene(window={'start_s':0.0,'end_s':86164.0905,'cadence_s':40000.0},limits={'min_altitude_deg':80.0,'sun_avoidance_deg':1.0,'min_window_s':0.0}))
 assert d['sample_times_s']==[0.0,40000.0,80000.0,86164.0905];assert [(w['start_s'],w['end_s']) for w in d['windows']]==[(0.0,0.0),(86164.0905,86164.0905)]
def test_minimum_window_yields_brief_status():
 """R4：最长窗口不足时状态应为 BRIEF 而非可观测。"""
 d,_=success(scene(window={'start_s':0.0,'end_s':7200.0,'cadence_s':3600.0},limits={'min_altitude_deg':80.0,'sun_avoidance_deg':1.0,'min_window_s':3600.0}))
 assert d['status']=='BRIEF';assert d['windows'][0]['duration_s']==0.0
def test_boolean_and_extra_key_rejection_preserve_two_outputs():
 """R1/R7：布尔伪数值和额外键拒收时不能污染报告或账本。"""
 b=scene();b['window']['cadence_s']=True;rejected(b);b=scene();b['target']['unexpected']=1;rejected(b)
def test_nonfinite_and_range_rejection_preserve_two_outputs():
 """R1/R7：非有限数及越界角度必须无副作用拒收。"""
 b=scene();b['observer']['latitude_deg']=float('nan');rejected(b);b=scene();b['sun']['ra_deg']=360.0;rejected(b)
def test_report_is_compact_sorted_lf_and_rounded():
 """R5：成功报告的字段、紧凑排序、换行和精度均是公共契约。"""
 d,raw=success(scene(target={'ra_deg':0.0,'dec_deg':10.0},sun={'ra_deg':123.456789,'dec_deg':-12.345678}))
 assert set(d)=={'observer_id','sample_times_s','status','sun_separation_deg','windows'};assert raw==json.dumps(d,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()+b'\n';assert b', ' not in raw;assert raw.endswith(b'\n')
def test_same_input_deduplicates_canonical_ledger_event():
 """R6：同一规范输入的账本事件必须幂等。"""
 x=scene()
 with tempfile.TemporaryDirectory() as raw:
  d=Path(raw);i=d/'in.json';o=d/'out.json';l=d/'ledger.jsonl';i.write_text(json.dumps(x),encoding='utf-8');cmd=[sys.executable,str(PROGRAM),'--input',str(i),'--output',str(o),'--ledger',str(l)]
  assert subprocess.run(cmd,capture_output=True,text=True).returncode==0;first=l.read_bytes();assert subprocess.run(cmd,capture_output=True,text=True).returncode==0;assert l.read_bytes()==first
  event=json.loads(first);assert event['fingerprint']==hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest();assert set(event)=={'fingerprint','observer_id','status'}
def test_distinct_input_appends_ledger_event():
 """R6：不同规范输入必须仅追加一个新账本事件。"""
 with tempfile.TemporaryDirectory() as raw:
  d=Path(raw);o=d/'out.json';l=d/'ledger.jsonl'
  for x in (scene(),scene(observer={'id':'OBS-8','latitude_deg':0.0,'longitude_deg':0.0,'sidereal_zero_deg':0.0})):
   i=d/'in.json';i.write_text(json.dumps(x),encoding='utf-8');p=subprocess.run([sys.executable,str(PROGRAM),'--input',str(i),'--output',str(o),'--ledger',str(l)],capture_output=True,text=True);assert p.returncode==0,p.stderr
  events=[json.loads(line) for line in l.read_text(encoding='utf-8').splitlines()];assert len(events)==2 and {v['observer_id'] for v in events}=={'OBS-7','OBS-8'}
def test_corrupt_ledger_preserves_report_ledger_and_directory():
 """R6/R7：不可解析账本是命名拒收，已有两个目标均应不变。"""
 p,o,l,files=run(scene(),b'prior report',b'not-json\n');assert p.returncode!=0 and p.stderr.splitlines()[0]=='REJECT LEDGER_READ';assert o==b'prior report' and l==b'not-json\n';assert files==['in.json','ledger.jsonl','report.json']
def test_success_is_byte_stable_without_temp_files():
 """R7：重复成功写入必须字节稳定且不残留程序临时文件。"""
 with tempfile.TemporaryDirectory() as raw:
  d=Path(raw);i=d/'in.json';o=d/'out.json';l=d/'ledger.jsonl';i.write_text(json.dumps(scene()),encoding='utf-8');cmd=[sys.executable,str(PROGRAM),'--input',str(i),'--output',str(o),'--ledger',str(l)]
  assert subprocess.run(cmd,capture_output=True,text=True).returncode==0;before=(o.read_bytes(),l.read_bytes());assert subprocess.run(cmd,capture_output=True,text=True).returncode==0;assert before==(o.read_bytes(),l.read_bytes());assert not [v for v in d.iterdir() if v.name.startswith('.visibility-')]
