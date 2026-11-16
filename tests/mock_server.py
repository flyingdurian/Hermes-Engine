"""Mock MLX for transport tests only; no real model performance claims."""
import sys, types, pathlib, tempfile, os
import numpy as np
root=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
# keep fake clone files outside production paths
home=tempfile.TemporaryDirectory(); os.environ['HOME']=home.name
voices=pathlib.Path(home.name)/'Projects/npc-tts-studio/voices'; voices.mkdir(parents=True)
(voices/'f09_tessa_blacksmith.wav').touch()
class Fake:
    sample_rate=24000
    def generate(self,text,voice=None,instruct=None,temperature=None,repetition_penalty=None,stream=False,streaming_interval=.5,language=None,ref_audio=None,speed=1):
        for _ in range(2 if stream else 1):
            yield types.SimpleNamespace(audio=(np.sin(np.arange(2400)*2*np.pi*440/24000)*.1).astype(np.float32))
mx=types.ModuleType('mlx'); core=types.ModuleType('mlx.core');core.clear_cache=lambda:None;mx.core=core
utils=types.ModuleType('mlx_audio.tts.utils');utils.load_model=lambda _:Fake()
for name,m in [('mlx',mx),('mlx.core',core),('mlx_audio',types.ModuleType('mlx_audio')),('mlx_audio.tts',types.ModuleType('mlx_audio.tts')),('mlx_audio.tts.utils',utils)]:sys.modules[name]=m
import game_tts_server as server
if __name__=='__main__':
    import uvicorn
    uvicorn.run(server.app,host='127.0.0.1',port=8765,log_level='warning')
