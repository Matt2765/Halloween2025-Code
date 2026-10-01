"""Persistent multichannel software mixer.  Public API is the 2025 API."""
from __future__ import annotations
import os, platform, subprocess, tempfile, threading, time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import sounddevice as sd
import soundfile as sf
from context import house
from utils.tools import log_event

PRIMARY_DEVICE_INDEX: Optional[int] = 3
SECONDARY_DEVICE_INDEX: Optional[int] = 6
FALLBACK_TO_SYSTEM_DEFAULT = True
MULTICH_MIN_CHANNELS = 6
SHORT_CLIP_MAX_SECONDS = 30.0
SHORT_CLIP_CACHE_MAX_BYTES = 256 * 1024 * 1024
STREAM_READ_FRAMES, STREAM_BUFFER_SECONDS, MIX_BLOCKSIZE = 16384, 3.0, 1024
hdmi_channels: Dict[str, Dict[str, Union[float, int, List[int]]]] = {
 "treasureRoom":{"index":0,"gain":1.0},"quarterdeck":{"index":1,"gain":0.6},"gangway":{"index":2,"gain":1.4},"HDMI_LFE":{"index":3,"gain":1.4},"HDMI_SL":{"index":4,"gain":1.6},"cargoHold":{"index":5,"gain":1.6},"HDMI_BL":{"index":6,"gain":1.8},"HDMI_BR":{"index":7,"gain":1.8}}
usb7_channels: Dict[str, Dict[str, Union[float, int, List[int]]]] = {
 "stereo_graveyard_L":{"index":0,"gain":1.0},"stereo_graveyard_R":{"index":1,"gain":1.0},"usb_C":{"index":2,"gain":1.0},"usb_LFE":{"index":3,"gain":1.0},"stereo_beckettPA_L":{"index":4,"gain":1.0},"stereo_beckettPA_R":{"index":5,"gain":1.0},"usb_BL":{"index":6,"gain":1.0},"usb_BR":{"index":7,"gain":1.0}}
DEFAULT_SOUND_DIR = (Path(__file__).resolve().parents[3] / "Assets" / "SoundDir").resolve()
_play_epoch = _cutoff_epoch = 0
_epoch_lock, _active_lock, _mixer_lock, _cache_lock = threading.Lock(), threading.Lock(), threading.Lock(), threading.Lock()
_stop_event = threading.Event()

class _Session:
 def __init__(self, epoch, label): self.epoch,self.label,self.done=epoch,label,threading.Event()
_active_sessions=[]

def text_to_wav(text: str, path: Path, rate: int = 0):
 system=platform.system()
 if system in ("Linux","Darwin"): subprocess.run(["espeak",f"-s{150+rate*10}","-w",str(path),text],check=True)
 elif system=="Windows":
  rate=max(-10,min(10,rate)); script=f'''Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate={rate};$s.SetOutputToWaveFile("{path}");$s.Speak("{text}");$s.Dispose()'''
  subprocess.run(["powershell","-NoProfile","-Command",script],check=True)
 else: raise RuntimeError(f"TTS not supported on {system}")
def _next_epoch():
 global _play_epoch
 with _epoch_lock: _play_epoch+=1; return _play_epoch
def _resolve_sound_path(value, base_folder=None):
 p=Path(value); return p if p.is_absolute() else ((Path(base_folder) if base_folder else DEFAULT_SOUND_DIR)/p).resolve()
def _normalise(x):
 x=np.asarray(x,np.float32); x=x[:,None] if x.ndim==1 else x
 return x if x.shape[1]<=2 else np.repeat(x.mean(axis=1,keepdims=True,dtype=np.float32),2,axis=1)
def _resample(x, src, dst):
 if src==dst or not len(x): return np.asarray(x,np.float32)
 n=max(1,round(len(x)*dst/src)); pos=np.minimum(np.arange(n,dtype=float)*src/dst,len(x)-1); original=np.arange(len(x),dtype=float)
 return np.column_stack([np.interp(pos,original,x[:,i]) for i in range(x.shape[1])]).astype(np.float32)

class _CachedSource:
 def __init__(self,data,looping): self.data,self.looping,self.position=data,looping,0; self.channels=data.shape[1]
 def read(self,frames):
  if not len(self.data): return np.zeros((frames,self.channels),np.float32),True
  if not self.looping:
   end=min(len(self.data),self.position+frames); block=self.data[self.position:end]; self.position=end; return block,end>=len(self.data)
  out=np.empty((frames,self.channels),np.float32); at=0
  while at<frames:
   take=min(frames-at,len(self.data)-self.position); out[at:at+take]=self.data[self.position:self.position+take]; at+=take; self.position=(self.position+take)%len(self.data)
  return out,False
 def close(self): pass

class _StreamedSource:
 """Reader thread owns SoundFile; callback sees only queued PCM."""
 def __init__(self,path,src_fs,dst_fs,channels,looping):
  self.path,self.src_fs,self.dst_fs,self.channels,self.looping=path,src_fs,dst_fs,channels,looping; self.blocks=deque(); self.offset=self.queued=0; self.lock=threading.Lock(); self.stop,self.eof,self.ready=threading.Event(),threading.Event(),threading.Event(); self.underruns=0; self.maximum=max(dst_fs,int(dst_fs*STREAM_BUFFER_SECONDS)); threading.Thread(target=self._reader,name=f"AudioReader:{path.name}",daemon=True).start()
 def _reader(self):
  try:
   with sf.SoundFile(str(self.path)) as f:
    while not self.stop.is_set():
     with self.lock: full=self.queued>=self.maximum
     if full: time.sleep(.01); continue
     raw=f.read(STREAM_READ_FRAMES,dtype="float32",always_2d=True)
     if not len(raw):
      if self.looping: f.seek(0); continue
      self.eof.set(); self.ready.set(); return
     block=_resample(_normalise(raw),self.src_fs,self.dst_fs)
     with self.lock:
      self.blocks.append(block); self.queued+=len(block)
      if self.queued>=min(self.dst_fs//4,self.maximum): self.ready.set()
  except Exception as e: self.eof.set(); self.ready.set(); log_event(f"[Audio] stream reader failed for '{self.path}': {e}")
 def read(self,frames):
  out=np.zeros((frames,self.channels),np.float32); at=0
  with self.lock:
   while at<frames and self.blocks:
    block=self.blocks[0]; take=min(frames-at,len(block)-self.offset); out[at:at+take]=block[self.offset:self.offset+take]; self.offset+=take; at+=take; self.queued-=take
    if self.offset==len(block): self.blocks.popleft(); self.offset=0
  if at<frames and not self.eof.is_set(): self.underruns+=1
  return (out[:at] if self.eof.is_set() else out), self.eof.is_set() and at<frames
 def close(self): self.stop.set()

@dataclass
class _Voice:
 source: object; mode: str; target: object; gain: float; session: _Session; honor_shutdown: bool; honor_breakcheck: bool; stopped: bool=False

class DeviceMixer:
 def __init__(self,kind,index,name,channels,samplerate,hostapi,fallback=False):
  self.kind,self.device_index,self.device_name,self.channels,self.samplerate,self.hostapi,self.fallback_to_all=kind,index,name,channels,samplerate,hostapi,fallback; self.voices=[]; self.lock=threading.Lock(); self.stream=None; self.callback_status_count=self.stream_underruns=self.peak_voices=0
 def start(self):
  def open_stream(extra): return sd.OutputStream(device=self.device_index,samplerate=self.samplerate,channels=self.channels,dtype="float32",blocksize=MIX_BLOCKSIZE,latency=.06,callback=self._callback,extra_settings=extra)
  extra=None
  if "wasapi" in self.hostapi.lower() and self.channels>=MULTICH_MIN_CHANNELS:
   try: extra=sd.WasapiSettings(exclusive=True)
   except Exception: pass
  try: self.stream=open_stream(extra); self.stream.start()
  except Exception as e:
   if extra is None: raise
   log_event(f"[Audio] {self.kind} WASAPI exclusive failed; shared fallback: {e}"); self.stream=open_stream(sd.WasapiSettings(exclusive=False)); self.stream.start()
  log_event(f"[Audio] {self.kind.upper()} persistent mixer opened idx={self.device_index} '{self.device_name}', fs={self.samplerate}, ch={self.channels}")
 def add(self,v):
  with self.lock: self.voices.append(v); self.peak_voices=max(self.peak_voices,len(self.voices))
  log_event(f"[Audio] voice started '{v.session.label}' on {self.kind.upper()}, active={self.active_voice_count}")
 @property
 def active_voice_count(self):
  with self.lock: return len(self.voices)
 def stop_matching(self,predicate):
  with self.lock:
   for v in self.voices:
    if predicate(v): v.stopped=True
 def _route(self,out,block,v):
  n=len(block)
  if not n:return
  b=block*v.gain
  if self.channels==1: out[:n,0]+=b[:,0]
  elif v.mode=="all": out[:n]+=b[:,:1]
  elif v.mode=="stereo": out[:n,int(v.target[0])]+=b[:,0]; out[:n,int(v.target[1])]+=b[:,0 if b.shape[1]==1 else 1]
  else: out[:n,min(int(v.target),self.channels-1)]+=b[:,0]
 def _callback(self,out,frames,time_info,status):
  out.fill(0)
  if status:
   self.callback_status_count+=1; self.stream_underruns+=int(bool(getattr(status,"output_underflow",False)))
  with self.lock: voices=tuple(self.voices)
  done=[]
  for v in voices:
   if v.stopped: done.append(v); continue
   block,finished=v.source.read(frames); self._route(out,block,v)
   if finished: done.append(v)
  np.clip(out,-1,1,out=out) # per-block hard safety; no AGC/pumping
  if done:
   with self.lock:
    for v in done:
     if v in self.voices: self.voices.remove(v); v.source.close(); v.session.done.set()
 def diagnostics(self):
  with self.lock: vs=tuple(self.voices)
  return {"device":self.device_index,"channels":self.channels,"samplerate":self.samplerate,"active_voices":len(vs),"peak_voices":self.peak_voices,"callback_statuses":self.callback_status_count,"callback_underruns":self.stream_underruns,"stream_buffer_underruns":sum(getattr(v.source,"underruns",0) for v in vs)}

def _host(index):
 try:
  for h in sd.query_hostapis():
   if index in h.get("devices",[]): return h.get("name","unknown")
 except Exception: pass
 return "unknown"
def _fixed(kind):
 index=PRIMARY_DEVICE_INDEX if kind=="primary" else SECONDARY_DEVICE_INDEX
 if index is None: raise RuntimeError(f"{kind.upper()}_DEVICE_INDEX not set or disabled.")
 d=sd.query_devices(index); channels=int(d["max_output_channels"])
 if channels<=0: raise RuntimeError(f"Configured {kind} device {index} has no output channels")
 return index,channels,int(round(float(d.get("default_samplerate",48000)))),_host(index),str(d.get("name","Unknown"))
_mixers={}
def _make_mixer(kind):
 index,ch,default,host,name=_fixed(kind); rate=48000 if "wasapi" in host.lower() and ch>=MULTICH_MIN_CHANNELS else default; mixer=DeviceMixer(kind,index,name,ch,rate,host)
 try: mixer.start(); return mixer
 except Exception as err:
  if not FALLBACK_TO_SYSTEM_DEFAULT: raise RuntimeError(f"Failed to open configured {kind} mixer: {err}") from err
  _,i=sd.default.device; d=sd.query_devices(i); ch=int(d["max_output_channels"])
  if ch<=0: raise RuntimeError(f"Configured mixer failed ({err}); system default has no output") from err
  log_event(f"[Audio] {kind.upper()} mixer failed ({err}); fallback idx={i}, all {ch} channel(s)"); mixer=DeviceMixer(kind,int(i),str(d.get("name","System Default")),ch,int(round(float(d.get("default_samplerate",48000)))),_host(int(i)),True); mixer.start(); return mixer
def _mixer(kind):
 with _mixer_lock:
  if kind not in _mixers: _mixers[kind]=_make_mixer(kind)
  return _mixers[kind]

_cache=OrderedDict(); _cache_bytes=0
def _cached(path,rate):
 global _cache_bytes
 key=(str(path.resolve()),rate)
 with _cache_lock:
  if key in _cache: _cache.move_to_end(key); return _cache[key]
 raw,fs=sf.read(str(path),dtype="float32",always_2d=True); data=_resample(_normalise(raw),int(fs),rate)
 with _cache_lock:
  _cache[key]=data; _cache_bytes+=data.nbytes
  while _cache and _cache_bytes>SHORT_CLIP_CACHE_MAX_BYTES: _,old=_cache.popitem(last=False); _cache_bytes-=old.nbytes
 return data
def _source(path,rate,looping,force_cached=False):
 info=sf.info(str(path)); duration=info.frames/info.samplerate
 if force_cached or duration<=SHORT_CLIP_MAX_SECONDS:return _CachedSource(_cached(path,rate),looping)
 source=_StreamedSource(path,int(info.samplerate),rate,1 if info.channels==1 else 2,looping); source.ready.wait(2); return source

def _pair(name,t):
 base=f"stereo_{name}"
 if base in t and isinstance(t[base].get("index"),(list,tuple)) and len(t[base]["index"])==2:return [int(x) for x in t[base]["index"]]
 l,r=f"{base}_L",f"{base}_R"
 if l in t and r in t and isinstance(t[l].get("index"),int) and isinstance(t[r].get("index"),int):return [int(t[l]["index"]),int(t[r]["index"])]
def _resolve_named_target(name):
 for kind,t in (("primary",hdmi_channels),("secondary",usb7_channels)):
  p=_pair(name,t)
  if p:
   base=f"stereo_{name}"; gain=float(t[base].get("gain",1)) if base in t else (float(t[f"{base}_L"].get("gain",1))+float(t[f"{base}_R"].get("gain",1)))/2; return kind,"stereo",p,gain
  if name in t:
   e=t[name]; ind=e["index"]
   if isinstance(ind,(list,tuple)) and len(ind)==2:return kind,"stereo",list(ind),float(e.get("gain",1))
   return kind,"one",int(ind),float(e.get("gain",1))
 raise ValueError(f"Unknown channel name '{name}'.")

def _submit(path,name,mode,target,gain,looping,honor_shutdown,honor_breakcheck,threaded,force_cached=False):
 kind="primary" if mode=="all" else _resolve_named_target(name)[0]
 if mode!="all": kind,mode,target,_=_resolve_named_target(name)
 m=_mixer(kind)
 if m.fallback_to_all: mode,target="all",0
 elif mode=="stereo": target=[min(int(x),m.channels-1) for x in target]
 else: target=min(int(target),m.channels-1)
 s=_Session(_next_epoch(),f"{path.name}@{name or 'all'}"); v=_Voice(_source(path,m.samplerate,looping,force_cached),mode,target,float(gain),s,honor_shutdown,honor_breakcheck)
 with _active_lock:_active_sessions.append(s)
 m.add(v)
 if not threaded:s.done.wait()

def play_to_named_channel(wav_file:str,target_name:str,*,gain_override:float|None=None,base_folder:Path|str|None=None,looping:bool=False,honor_shutdown:bool=True,honor_breakcheck:bool=True,threaded:bool=True):
 path=_resolve_sound_path(wav_file,Path(base_folder) if base_folder else DEFAULT_SOUND_DIR)
 if not path.exists():raise FileNotFoundError(path)
 gain=_resolve_named_target(target_name)[3] if gain_override is None else gain_override; log_event(f"[Audio] Playing '{path.name}' -> {target_name}, gain={gain}, looping={looping}, threaded={threaded}"); _submit(path,target_name,"one",0,gain,looping,honor_shutdown,honor_breakcheck,threaded)
def play_to_all_channels(wav_or_text:str,*,tts_rate:int=0,gain_override:float|None=None,base_folder:Path|str|None=None,looping:bool=False,honor_shutdown:bool=True,honor_breakcheck:bool=True,threaded:bool=True):
 base=Path(base_folder) if base_folder else DEFAULT_SOUND_DIR; path=_resolve_sound_path(wav_or_text,base); gain=1 if gain_override is None else gain_override
 if Path(wav_or_text).suffix.lower()==".wav" or path.exists():
  if not path.exists():raise FileNotFoundError(path)
  return _submit(path,None,"all",0,gain,looping,honor_shutdown,honor_breakcheck,threaded)
 fd,tmp=tempfile.mkstemp(suffix=".wav");os.close(fd); path=Path(tmp)
 try:text_to_wav(wav_or_text,path,tts_rate);_submit(path,None,"all",0,gain,False,False,False,True,True)
 finally:
  try:path.unlink()
  except OSError:pass
def play_audio(target_or_text:str,maybe_file:str|None=None,*,gain:float|None=None,base_folder:Path|str|None=None,tts_rate:int=0,looping:bool=False,threaded:bool=True):
 if maybe_file:
  return play_to_all_channels(maybe_file,gain_override=gain,base_folder=base_folder,looping=looping,threaded=threaded) if target_or_text.lower()=="all" else play_to_named_channel(maybe_file,target_or_text,gain_override=gain,base_folder=base_folder,looping=looping,threaded=threaded)
 if ":" in target_or_text:
  name,text=(x.strip() for x in target_or_text.split(":",1))
  try:_resolve_named_target(name)
  except ValueError:pass
  else:
   fd,tmp=tempfile.mkstemp(suffix=".wav");os.close(fd);path=Path(tmp)
   try:text_to_wav(text,path,tts_rate);return _submit(path,name,"one",0,1 if gain is None else gain,False,False,False,True,True)
   finally:
    try:path.unlink()
    except OSError:pass
 return play_to_all_channels(target_or_text,tts_rate=tts_rate,gain_override=gain,base_folder=base_folder,honor_shutdown=False,honor_breakcheck=False,threaded=True)
def _break_monitor():
 while True:
  try:
   # BreakCheck() reports every failed check.  This daemon polls frequently so
   # using it here turned an offline state into ~20 log/debug entries a second.
   # Keep the same condition, but leave reporting to the foreground caller
   # that actually needs diagnostic context.
   if not house.HouseActive or house.systemState != "ONLINE":
    for m in tuple(_mixers.values()):m.stop_matching(lambda v:v.honor_breakcheck)
  except Exception as e:log_event(f"[Audio] BreakCheck monitor error: {e}")
  time.sleep(.05)
threading.Thread(target=_break_monitor,name="AudioBreakCheck",daemon=True).start()
def stop_all_audio(timeout:float=2.0):
 global _cutoff_epoch
 with _epoch_lock:_cutoff_epoch=_play_epoch
 _stop_event.set()
 for m in tuple(_mixers.values()):m.stop_matching(lambda v:v.honor_shutdown)
 log_event(f"[Audio] stop_all_audio(): cutoff={_cutoff_epoch}"); deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  with _active_lock:_active_sessions[:]=[s for s in _active_sessions if not s.done.is_set()]; pending=bool(_active_sessions)
  if not pending:break
  time.sleep(.01)
 _stop_event.clear();log_event("[Audio] stop_all_audio(): complete")
def list_output_devices():return [f"[{i}] {d['name']} ({d['max_output_channels']}ch)" for i,d in enumerate(sd.query_devices()) if d.get("max_output_channels",0)>0]
def list_named_channels():
 result={}
 for key,value in hdmi_channels.items():result[key]={"index":value["index"],"gain":value["gain"],"device":"primary"}
 for key,value in usb7_channels.items():result[key]={"index":value["index"],"gain":value["gain"],"device":"secondary"}
 return result
def register_hdmi_channel(name,index,gain=1.0):
 if name in usb7_channels:raise ValueError(f"'{name}' exists in usb7_channels")
 hdmi_channels[name]={"index":index,"gain":gain}
def register_usb7_channel(name,index,gain=1.0):
 if name in hdmi_channels:raise ValueError(f"'{name}' exists in hdmi_channels")
 usb7_channels[name]={"index":index,"gain":gain}
def set_channel_gain(name,gain):
 if name in hdmi_channels:hdmi_channels[name]["gain"]=gain;return
 if name in usb7_channels:usb7_channels[name]["gain"]=gain;return
 raise ValueError(f"Unknown channel '{name}'")
def audio_diagnostics():return {kind:m.diagnostics() for kind,m in _mixers.items()}
