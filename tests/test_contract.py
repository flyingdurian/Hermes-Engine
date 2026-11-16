import unittest, io, numpy as np, soundfile as sf
from fastapi.testclient import TestClient
from mock_server import server
class Contract(unittest.TestCase):
    def setUp(self): self.client=TestClient(server.app)
    def test_stream_and_wav(self):
        for engine,voice in [('qwen','Vivian'),('chatterbox','f09_tessa_blacksmith'),('kokoro','af_heart')]:
            for endpoint in ['/stream','/v1/audio/speech']:
                r=self.client.post(endpoint,json={'engine':engine,'voice':voice,'input':'Hello traveler. What is your price?','mood':'happy','pitch_shift':1,'stream_chunk_min':10,'model_id':server.MODELS[engine]})
                self.assertEqual(r.status_code,200,r.text[:200] if r.status_code!=200 else '')
                m=self.client.get('/runs/'+r.headers['X-Run-Id']).json();self.assertEqual(m['status'],'complete');self.assertGreater(m['audio_duration_s'],0);self.assertGreater(len(m['chunks']),0)
                if endpoint=='/stream': self.assertEqual(len(r.content)/2/24000,m['audio_duration_s'])
                else: self.assertEqual(sf.read(io.BytesIO(r.content))[1],24000)
                if engine=='chatterbox': self.assertIn('[chuckle]',m['effective_text'])
                if engine=='kokoro': self.assertIn('temperature',m['ignored_controls'])
    def test_validation(self):
        for p in [{'engine':'invalid'},{'engine':'chatterbox','voice':'missing'},{'pitch_shift':9},{'unknown':1},{'input':'[laugh]'}]:
            r=self.client.post('/stream',json={'input':'Hello',**p});self.assertEqual(r.status_code,422)
    def test_reload(self):
        p={'input':'Hello','voice':'Vivian','force_reload':True}; r=self.client.post('/stream',json=p); self.assertEqual(r.headers['X-Model-State'],'forced-reload');p['force_reload']=False;r=self.client.post('/stream',json=p);self.assertEqual(r.headers['X-Model-State'],'warm')
    def test_pitch(self):
        a=np.sin(np.arange(24000)*2*np.pi*440/24000).astype(np.float32); b=server.shape_audio(a,1,4);self.assertEqual(len(a),len(b));freq=np.argmax(abs(np.fft.rfft(b)));self.assertAlmostEqual(freq,554,delta=8)
if __name__=='__main__':unittest.main()
