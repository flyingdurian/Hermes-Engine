"""Real Mac acceptance test. No mocked timings. Run while server is active."""
import argparse, json, pathlib, time, urllib.request, urllib.error
p=argparse.ArgumentParser();p.add_argument('--url',default='http://127.0.0.1:8000');p.add_argument('--all-voices',action='store_true');p.add_argument('--out',default='mac_smoke_results.json');a=p.parse_args();base=a.url.rstrip('/')
manifest=json.loads(pathlib.Path(__file__).with_name('voice_manifest.json').read_text()); selected=manifest if a.all_voices else [manifest[8]]
def get(path):
    with urllib.request.urlopen(base+path,timeout=600) as r:return json.load(r)
health=get('/health');assert health['api_version']=='3.0',health
results=[]
for engine in ('qwen','chatterbox','kokoro'):
    for n in selected:
        voice=n['id'] if engine=='chatterbox' else n[engine]; payload={'engine':engine,'voice':voice,'input':'Welcome, traveler. What brings you here today?','mood':'happy','instruct':'Speak warmly and cheerfully.','speed':1,'pitch_shift':0,'temperature':.45,'repetition_penalty':1.3,'stream_chunk_min':85}
        try:
            t=time.perf_counter(); req=urllib.request.Request(base+'/stream',json.dumps(payload).encode(),{'Content-Type':'application/json'});first=None;size=0
            with urllib.request.urlopen(req,timeout=600) as r:
                run_id=r.headers['X-Run-Id'];rate=int(r.headers['X-Sample-Rate'])
                while chunk:=r.read1(4096):
                    if first is None:first=time.perf_counter()-t
                    size+=len(chunk)
            m=get('/runs/'+run_id);assert m['status']=='complete' and size>0 and size%2==0;assert abs(size/2/rate-m['audio_duration_s'])<1e-6
            results.append({'npc':n['id'],'client_ttfa_s':first,**m});print(f'PASS {engine} {voice}: TTFA={m["ttfa_s"]:.3f}s load={m["load_s"]:.3f}s',flush=True)
        except Exception as e:
            results.append({'npc':n['id'],'engine':engine,'voice':voice,'error':str(e)});print(f'FAIL {engine} {voice}: {e}',flush=True)
pathlib.Path(a.out).write_text(json.dumps(results,indent=2));fail=sum('error' in r for r in results);print(f'{len(results)-fail}/{len(results)} passed; report {a.out}');raise SystemExit(bool(fail))
