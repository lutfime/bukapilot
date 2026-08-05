import sys; sys.path.insert(0,"/data/openpilot")
import cereal.messaging as messaging
import time
sm = messaging.SubMaster(["carState","carControl","controlsState","liveTorqueParameters","selfdriveState"])
def g(o,*p,default="-"):
  cur=o
  for x in p:
    try: cur=getattr(cur,x)
    except Exception: return default
  return cur
t0=time.time(); last=-1
print("t  vEgo(kmh) steerCmd  accel  angle   trqTorque  latCtrl | torqued: useParams liveValid latAccelFactor calPerc%")
while time.time()-t0<30:
  sm.update(0)
  s=int(time.time()-t0)
  if s!=last and sm.updated["carState"]:
    last=s
    cs=sm["carState"]; cc=sm["carControl"]; cst=sm["controlsState"]; ltp=sm["liveTorqueParameters"]
    lat=cst.lateralControlState; lw=lat.which(); lw=lw() if callable(lw) else lw
    print(f"{s:02d} {cs.vEgo*3.6:7.1f} {g(cc.actuators,'torque'):+8.2f} {g(cc.actuators,'accel'):+6.2f} {cs.steeringAngleDeg:+6.1f} {cs.steeringTorque:+8.1f}  {lw} | use={g(ltp,'useParams')} valid={g(ltp,'liveValid')} factor={g(ltp,'latAccelFactorFiltered')} cal={g(ltp,'calPerc')}")
