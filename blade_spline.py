# =============================================================================
# blade_spline.py — Numpy-only cubic spline for blade load interpolation
# =============================================================================
import numpy as np


def _thomas(a, b, c, d):
    n = len(d); c_ = np.zeros(n); d_ = np.zeros(n); x = np.zeros(n)
    c_[0] = c[0]/b[0]; d_[0] = d[0]/b[0]
    for i in range(1, n):
        den = b[i] - a[i]*c_[i-1]
        c_[i] = c[i]/den if i < n-1 else 0.0
        d_[i] = (d[i] - a[i]*d_[i-1])/den
    x[n-1] = d_[n-1]
    for i in range(n-2, -1, -1):
        x[i] = d_[i] - c_[i]*x[i+1]
    return x


def fit_blade_spline(radii, values):
    x = np.asarray(radii, dtype=float); y = np.asarray(values, dtype=float)
    n = len(x); h = np.diff(x)
    if n < 3:
        return {'x': x, 'y': y, 'M': np.zeros(n), 'h': h}
    n_int = n - 2
    rhs = np.array([6.0*((y[i+2]-y[i+1])/h[i+1]-(y[i+1]-y[i])/h[i])
                    for i in range(n_int)])
    diag = 2.0*(h[:n_int]+h[1:n_int+1])
    a = np.zeros(n_int); a[1:] = h[1:n_int]
    b = diag
    c = np.zeros(n_int); c[:-1] = h[1:n_int]
    M = np.zeros(n); M[1:-1] = _thomas(a, b, c, rhs)
    return {'x': x, 'y': y, 'M': M, 'h': h}


def eval_spline(sp, xq):
    x = sp['x']; y = sp['y']; M = sp['M']; h = sp['h']
    xq = float(np.clip(xq, x[0], x[-1]))
    i  = int(np.clip(np.searchsorted(x, xq, 'right')-1, 0, len(x)-2))
    dx = xq-x[i]; hi = h[i]; ai = (x[i+1]-xq)/hi; bi = dx/hi
    return float(ai*y[i]+bi*y[i+1]+((ai**3-ai)*M[i]+(bi**3-bi)*M[i+1])*hi**2/6.0)


def nearest_idx(radii, target_r):
    return int(np.argmin(np.abs(np.asarray(radii, dtype=float)-target_r)))


def interpolate_blade_loads(radii, load_values, dlc_sources, blade_tags,
                              plf_values, blade_length,
                              fractions=(0.0, 0.25, 0.50, 0.75)):
    """Cubic spline interpolation at radial fractions. DLC from nearest station."""
    radii_arr = np.asarray(radii, dtype=float)
    vals_arr  = np.array([v if v is not None else np.nan
                          for v in load_values], dtype=float)
    valid = ~np.isnan(vals_arr)
    if valid.sum() < 2:
        return []
    sp = fit_blade_spline(radii_arr[valid], vals_arr[valid])
    results = []
    for frac in fractions:
        r     = frac * blade_length
        label = 'Root' if frac == 0.0 else f'{int(round(frac*100))}%'
        val   = (float(vals_arr[0]) if frac == 0.0 and valid[0]
                 else eval_spline(sp, r))
        ni    = nearest_idx(radii_arr, r)
        results.append({
            'label'    : label,
            'radius_m' : r,
            'value'    : val,
            'dlc'      : dlc_sources[ni] if dlc_sources else None,
            'blade_tag': blade_tags[ni]  if blade_tags  else None,
            'plf'      : plf_values[ni]  if plf_values  else None,
        })
    return results
