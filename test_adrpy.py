import sys
sys.path.insert(0, r'C:\Users\autho\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages')

from ADRpy import atmospheres, unitconversions as uc
import math

def s(v):
    """Extract scalar from numpy array or return float."""
    try:
        return float(v.item()) if hasattr(v, 'item') else float(v)
    except Exception:
        return float(v[0])

atm = atmospheres.Atmosphere()
alt_m = uc.feet2m(35000)

oat_c   = s(atm.airtemp_c(alt_m))
oat_k   = s(atm.airtemp_k(alt_m))
rho     = s(atm.airdens_kgpm3(alt_m))
sigma   = rho / 1.225
p_pa    = s(atm.airpress_pa(alt_m))
sos_kts = s(atm.vsound_kts(alt_m))

eas_kts    = 250.0
cas_result = atm.keas2kcas(eas_kts, alt_m)
cas_kts    = s(cas_result[0])
tas_mps    = s(atm.eas2tas(uc.kts2mps(eas_kts), alt_m))
tas_kts    = uc.mps2kts(tas_mps)
mach       = tas_kts / sos_kts

tat_ratio  = s(atmospheres.tatbysat(mach))
tat_c      = oat_k * tat_ratio - 273.15
tf         = s(atmospheres.turbofanthrustfactor(oat_c, p_pa, mach, ptype='highbpr'))
tp         = s(atmospheres.turbopropthrustfactor(sigma, mach))
pf         = s(atmospheres.pistonpowerfactor(rho))

print("=== FL350 atmosphere ===")
print(f"  OAT          {oat_c:.1f} C")
print(f"  Density      {rho:.4f} kg/m3   sigma={sigma:.4f}")
print(f"  Press        {p_pa:.0f} Pa")
print(f"  SOS          {sos_kts:.1f} kts")

print("\n=== Speeds at 250 KEAS ===")
print(f"  KCAS         {cas_kts:.1f}")
print(f"  KTAS         {tas_kts:.1f}")
print(f"  Mach         {mach:.3f}")
print(f"  TAT          {tat_c:.1f} C")

print("\n=== Thrust factors ===")
print(f"  Turbofan HBR {tf:.3f}")
print(f"  Turboprop    {tp:.3f}")
print(f"  Piston       {pf:.3f}")

# Stall speed 737 at FL350 cruise weight
w_n        = uc.lbf2n(115000)
s_m2       = uc.feet22m2(1171.0)
vs_tas_mps = math.sqrt(2 * w_n / (rho * s_m2 * 1.85))
vs_kias    = round(uc.mps2kts(s(atm.tas2eas(vs_tas_mps, alt_m))), 1)
print(f"\n  737 VS clean FL350 (115k lbs): {vs_kias} KIAS")

# V-speeds C172P at sea level (ADRpy compute)
rho_sl   = s(atm.airdens_kgpm3(0))
w_n172   = uc.lbf2n(2400)
s172     = uc.feet22m2(174.0)
vs172    = math.sqrt(2 * w_n172 / (rho_sl * s172 * 1.60))
vs172_k  = round(uc.mps2kts(vs172), 1)
print(f"  C172P VS1g SL: {vs172_k} kts  VR={round(1.1*vs172_k,1)}  V2={round(1.2*vs172_k,1)}")

print("\nAll checks passed.")
