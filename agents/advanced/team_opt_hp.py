"""Model predictive control (30 hour receding horizon) of a portfolio's batteries, trades and heat pumps.

Each hour a linear program plans battery flows, market bids and, for homes with a fitted RC model,
heat pump power; only the first hour is applied. The heat pump command is the coasting rule moved by
at most TRUST towards the plan.
Forecasts: price = published daily shape x today's factor; load, PV and weather = same hour of the
last three days (seasonal naive); market clearing ratio and volume = exponential smoothing by hour.
Building: published linear RC model (ARX) with an exponentially smoothed bias correction.
The first two days use simple rules until there is history.
"""
import numpy as np
from scipy.optimize import linprog
from annex96p2p.stock_agents import StorageServiceAgent

NAME = "team_opt_hp"
BUILDINGS = ["resstock-amy2018-2021-release-1-112208",
             "resstock-amy2018-2021-release-1-179247",
             "resstock-amy2018-2021-release-1-199613",
             "resstock-amy2018-2021-release-1-147002"]

SHAPE = np.array([0.14, 0.13, 0.12, 0.11, 0.12, 0.15, 0.24, 0.34, 0.30, 0.20, 0.12, 0.08,
                  0.06, 0.055, 0.07, 0.12, 0.22, 0.36, 0.45, 0.40, 0.29, 0.22, 0.18, 0.16])
PRIOR_VOL = np.array([0, 0, 0, 0, 0, 0, 12, 10, 27, 77, 101, 101, 77, 65, 25, 3,
                      37, 41, 42, 42, 42, 38, 0, 0], float)
HORIZON, START = 30, 48
DEPTH_SHARE = 3.0      # share of the last observed market volume we assume we can trade
TERMINAL = 0.4         # value of energy left in the battery at the horizon, vs the best price
CYCLE = 0.003          # $/kWh battery wear, stops simultaneous charge and discharge
ASK_FRAC = 0.8         # asking price in a peak, as a share of the grid price
EVENING_AIM, MORNING_AIM = -0.9, -0.5     # coasting: thermostat aim in the price peaks
MARGIN = 0.6           # planned temperature stays this far inside the band (C)
PENALTY = 0.75         # $/C-h charged in the plan for leaving the band (the bill charges 0.50)
TRUST = 0.25           # most the plan may move the heat pump command from the coasting rule

# Published one zone RC fits, houses with validation error <= 1.5 C:
# T_next = T + c . [Tout - T, pump heat kW, irradiance, base load, sin(hour), cos(hour), 1]
RC = {
    "112208": [0.291, 0.6143, 0.7104, 0.0853, 0.1801, 0.1626, 5.2002],
    "147002": [0.3498, 0.6317, 2.3437, 0.1035, -0.2112, 0.2322, 5.3962],
    "179247": [0.0989, 0.8471, 1.2818, 0.0285, -0.0555, 0.0308, 0.3936],
    "199613": [0.2398, 0.6768, 0.3352, -0.0641, -0.0793, 0.2644, 4.1035],
    "20199": [0.267, 0.5737, 1.0509, -0.1328, 0.0294, -0.3846, 1.6571],
    "216895": [0.117, 0.9441, -0.0153, 0.1604, -0.1088, -0.1851, 1.3644],
    "223581": [0.1518, 0.9504, 1.7504, -0.0026, -0.1643, 0.1633, 1.4196],
    "247942": [0.0365, 0.6572, 0.4323, -0.0477, -0.1487, 0.0824, -1.4348],
    "319918": [0.0696, 0.7546, 0.5127, -0.0775, -0.256, 0.0377, -0.7312],
    "376570": [0.2047, 0.7417, 1.3376, 0.1777, -0.0383, -0.1692, -0.6882],
    "409896": [0.1319, 0.5991, 0.7459, 0.0192, 0.0081, 0.2539, 1.5166],
    "411001": [0.4151, 1.334, -2.7172, -0.5366, -0.1924, -1.1073, 8.4639],
    "425540": [0.4434, 0.8423, 1.4654, 0.0393, 0.0498, 0.1787, 5.3875],
    "4421": [0.2683, 0.5972, 0.5642, -0.0548, -0.1593, 0.088, 4.0184],
    "460412": [0.3268, 0.5462, 0.4408, -0.088, 0.2272, 0.1029, 5.131],
    "467125": [0.1642, 0.6589, 0.8836, 0.0055, -0.2631, 0.0022, 1.6205],
    "481052": [0.3249, 0.9087, -2.3162, -0.0883, 0.2508, -0.0384, 3.7059],
    "485614": [0.2723, 1.462, 0.9473, -0.0464, -0.1224, -0.4527, 3.5985],
}


class OptHPAgent(StorageServiceAgent):

    def reset(self):
        super().reset()
        self.ratio, self.vol = np.full(24, 0.6), PRIOR_VOL.copy()   # clearing price / grid price, volume
        self.ex, self.bias, self.pred = {}, {}, {}
        self.last_grid, self.sol = None, None

    def topup(self, o, mkt, need, peak):
        """Overnight, fill to 80% for the morning peak (the default holds a quarter)."""
        if mkt.hour > 5 or mkt.grid_import_price > 0.15:
            return super().topup(o, mkt, need, peak)
        eta, cap = 0.9 ** 0.5, o.battery_capacity_kwh
        morning = self.peak_need(o, True, need, False) or 0.0
        target = min(0.95 * cap, max(0.8 * cap, 0.10 * cap + morning / eta))
        return max(0.0, target - o.battery_soc_kwh) / eta / (6 - mkt.hour)

    def _pace(self, obs, mkt, net):
        """Fallback plan: spend stored energy over a peak in proportion to price^3.5, not evenly."""
        if mkt.grid_import_price < 0.20:
            return net
        w = SHAPE[mkt.hour:(9 if mkt.hour < 12 else 21) + 1] ** 3.5
        net = 0.0
        for name, o in obs.items():
            p = self.plan[name]
            if p["battery"] < 0:
                stored = max(0.0, o.battery_soc_kwh - 0.10 * o.battery_capacity_kwh)
                p["battery"] = -min(p["discharge"], stored * 0.9 ** 0.5 * w[0] / w.sum())
                p["floor"] = p["need"] + p["battery"]
            net += p["need"] + p["battery"]
        return net

    def _learn(self, mkt):
        if self.last_grid and mkt.timestep > 0:
            h = (mkt.hour - 1) % 24
            if mkt.last_clearing_price is not None:
                self.ratio[h] += 0.2 * (mkt.last_clearing_price / self.last_grid - self.ratio[h])
            self.vol[h] += 0.2 * (mkt.last_clearing_volume - self.vol[h])
        self.last_grid = mkt.grid_import_price

    def _coef(self, o):
        c = RC.get(o.name.split("-")[-1])
        return None if c is None or not o.hp_is_heating else np.asarray(c, float)

    def _look(self, name, key, step, t):
        """Mean of `key` at the same hour on the last three days (steps <= t)."""
        rec = self.ex[name]
        vals = [rec[s][key] for s in (step - 24, step - 48, step - 72) if s <= t and s in rec]
        return float(np.mean(vals)) if vals else None

    def _solve(self, obs, mkt):
        t, H, eta = mkt.timestep, HORIZON, 0.9 ** 0.5
        cap = sum(o.battery_capacity_kwh for o in obs.values())
        if cap <= 0:
            return None
        th = [n for n, o in obs.items() if self._coef(o) is not None]
        J, hr = len(th), (mkt.hour + np.arange(H)) % 24
        N = np.zeros(H)                      # portfolio energy before batteries and modelled pumps
        for n, o in obs.items():
            for k in range(H):
                if k == 0:
                    x = self.forecast(o) + (0.0 if n in th else o.hp_draw_kwh(self.heat_command(o, mkt)))
                else:
                    b, pv = self._look(n, "base", t + k, t), self._look(n, "pv", t + k + 1, t)
                    if b is None or pv is None:
                        return None
                    x = b - pv
                N[k] += x + (0.0 if n in th or k == 0 else self._look(n, "hp", t + k, t) or 0.0)
        g = SHAPE[hr] * (mkt.grid_import_price / SHAPE[mkt.hour])
        rho, depth = self.ratio[hr], np.maximum(2.0, DEPTH_SHARE * self.vol[hr])

        # variable blocks of H hours: c, d (battery in, out), gb, mb (grid, market buys),
        # ms, cu (sales), soc, then T, P, slack low, slack high for each modelled pump
        nv, col = 7 * H + 4 * H * J, lambda q: slice(q * H, (q + 1) * H)
        I, S = np.eye(H), np.eye(H, k=-1)
        cost, lo, hi = np.zeros(nv), np.zeros(nv), np.full(nv, np.inf)
        cost[col(0)] = cost[col(1)] = CYCLE
        cost[col(2)], cost[col(3)], cost[col(4)] = g, rho * g, -rho * g
        cost[7 * H - 1] -= TERMINAL * g.max() * float(np.mean(rho))
        hi[col(0)] = hi[col(1)] = sum(o.battery_max_power_kw for o in obs.values())
        hi[0] = sum(self.limits(o)[1] for o in obs.values())      # this hour: what the batteries can do now
        hi[H] = sum(self.limits(o)[0] for o in obs.values())
        hi[col(3)] = hi[col(4)] = depth
        lo[col(6)], hi[col(6)] = 0.10 * cap, 0.95 * cap

        A, b = np.zeros((2 * H, nv)), np.concatenate(([sum(o.battery_soc_kwh for o in obs.values())], np.zeros(H - 1), -N))
        A[:H, col(0)], A[:H, col(1)], A[:H, col(6)] = -eta * I, I / eta, I - S      # battery state of charge
        A[H:, col(0)], A[H:, col(1)], A[H:, col(2)] = I, -I, -I                     # energy balance
        A[H:, col(3)], A[H:, col(4)], A[H:, col(5)] = -I, I, I
        Ath, bth, Aub, bub = [], [], [], []
        for j, n in enumerate(th):
            o, c, q = obs[n], self._coef(obs[n]), 7 + 4 * j
            A[H:, col(q + 1)] = I
            drive, tlo, thi = [], [], []
            for k in range(H):
                if k == 0:
                    tout, irr, base, lo_k, hi_k = (o.outdoor_temperature, o.solar_irradiance, o.base_load_kwh,
                                                   o.comfort_low, o.comfort_high)
                else:                                   # the band applies to the end of the hour
                    tout, irr, base = (self._look(n, key, t + k, t) for key in ("tout", "irr", "base"))
                    lo_k, hi_k = (self._look(n, key, t + k + 1, t) for key in ("lo", "hi"))
                    if None in (tout, irr, base, lo_k, hi_k):
                        return None
                drive.append(c[0] * tout + c[2] * irr + c[3] * base + c[4] * np.sin(2 * np.pi * hr[k] / 24)
                             + c[5] * np.cos(2 * np.pi * hr[k] / 24) + c[6] + self.bias.get(n, 0.0))
                tlo.append(lo_k); thi.append(hi_k)
            drive, tlo, thi = np.array(drive), np.array(tlo), np.array(thi)
            drive[0] += (1 - c[0]) * o.indoor_temperature
            row = np.zeros((H, nv))                     # T[k+1] = (1 - c0) T[k] + c1 COP P[k] + drive[k]
            row[:, col(q)], row[:, col(q + 1)] = I - (1 - c[0]) * S, -c[1] * o.hp_cop * I
            Ath.append(row); bth.append(drive)
            hi[col(q)], hi[col(q + 1)] = 45, o.hp_max_electric_kw
            cost[col(q + 2)] = cost[col(q + 3)] = PENALTY
            for sign, slack, rhs in ((-1, q + 2, -(tlo + MARGIN)), (1, q + 3, thi - MARGIN)):
                row = np.zeros((H, nv))                 # T + slack_lo >= lo + margin, T - slack_hi <= hi - margin
                row[:, col(q)], row[:, col(slack)] = sign * I, -I
                Aub.append(row); bub.append(rhs)
        res = linprog(cost, A_ub=np.vstack(Aub) if J else None, b_ub=np.concatenate(bub) if J else None,
                      A_eq=np.vstack([A] + Ath), b_eq=np.concatenate([b] + bth),
                      bounds=np.column_stack([lo, hi]), method="highs")
        if res.status != 0:
            return None
        x = res.x
        return dict(gb=x[2 * H], mb=x[3 * H], ms=x[4 * H], cu=x[5 * H], rho=rho[0], c=x[0], d=x[H],
                    P={n: x[(8 + 4 * j) * H] for j, n in enumerate(th)})

    def heat_command(self, o, mkt):
        aim = None
        if mkt.grid_import_price >= 0.20:               # coast through the price peaks
            aim = EVENING_AIM if 16 <= mkt.hour <= 21 else MORNING_AIM if 6 <= mkt.hour <= 9 else None
        base = self.thermostat(o, aim=aim)
        cmd, c, sol = base, self._coef(o), self.sol
        if sol is not None and c is not None and o.name in sol["P"]:
            plan = sol["P"][o.name] / max(o.hp_max_electric_kw, 1e-6)
            cmd = base + float(np.clip(plan - base, -TRUST, TRUST))
            if o.indoor_temperature < o.comfort_low + 0.3:
                cmd = max(cmd, base)                   # near the floor or ceiling: follow the thermostat
            if o.indoor_temperature > o.comfort_high - 0.3:
                cmd = min(cmd, base)
            cmd = float(np.clip(cmd, 0.0, 1.0))
        if c is not None:                              # remember the model's prediction to learn its bias
            x = np.array((o.outdoor_temperature - o.indoor_temperature, o.hp_draw_kwh(cmd) * o.hp_cop,
                          o.solar_irradiance, o.base_load_kwh,
                          np.sin(o.hour * 2 * np.pi / 24), np.cos(o.hour * 2 * np.pi / 24), 1.0))
            self.pred[o.name] = (o.timestep, o.indoor_temperature + float(x @ c) + self.bias.get(o.name, 0.0))
        return cmd

    def plan_hour(self, obs, mkt):
        self._learn(mkt)
        t = mkt.timestep
        for name, o in obs.items():
            prev = self.pred.get(name)
            if prev is not None and prev[0] == t - 1:
                resid = float(np.clip(o.indoor_temperature - prev[1], -3.0, 3.0))
                self.bias[name] = float(np.clip(0.8 * self.bias.get(name, 0.0) + 0.2 * resid, -2.0, 2.0))
            self.ex.setdefault(name, {})[t] = dict(
                base=o.base_load_kwh, pv=o.solar_generation_kwh, tout=o.outdoor_temperature,
                irr=o.solar_irradiance, lo=o.comfort_low, hi=o.comfort_high, hp=0.0)
        self.sol = self._solve(obs, mkt) if t >= START else None
        net = super().plan_hour(obs, mkt)              # the default plan, with the heat_command above
        for name, o in obs.items():
            self.ex[name][t]["hp"] = o.hp_draw_kwh(self.plan[name]["hvac"])
        sol = self.sol
        if sol is None:
            return self._pace(obs, mkt, net)
        x = sol["c"] - sol["d"]                        # portfolio battery flow this hour, shared by capability
        pool = {n: (p["charge"] if x >= 0 else p["discharge"]) for n, p in self.plan.items()}
        total = sum(pool.values()) or 1.0
        needs = {n: max(p["need"], 0.0) for n, p in self.plan.items()}
        ntot, net = sum(needs.values()), 0.0
        for name, p in self.plan.items():
            bat = abs(x) * pool[name] / total
            p["battery"] = bat if x >= 0 else -bat
            share = needs[name] / ntot if ntot > 0 else 1.0 / len(self.plan)
            p["firm"] = max(0.0, p["battery"])
            p["floor"] = p["need"] + p["battery"] - sol["mb"] * share
            net += p["need"] + p["battery"]
        return net

    def bid(self, obs, mkt):
        net = self.plan_hour(obs, mkt)
        grid, sol, bid = mkt.grid_import_price, self.sol, mkt.new_bid()
        ask = ASK_FRAC * grid if grid >= 0.20 else 0.01
        if sol is None:                                # first two days
            if net >= 0:
                bid.buy(grid, net)
            else:
                bid.sell(ask, -net)
            return bid.array
        for side, price, kwh in ((bid.buy, grid, sol["gb"]),                       # energy we must draw
                                 (bid.buy, max(0.01, sol["rho"] * grid), sol["mb"]),  # energy for storage
                                 (bid.sell, ask, sol["ms"]),
                                 (bid.sell, 0.01, sol["cu"])):                     # surplus beyond market depth
            if kwh > 1e-6:
                side(price, kwh)
        return bid.array


AGENT = OptHPAgent
