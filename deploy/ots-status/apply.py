#!/usr/bin/env python3
"""Add a live Services table and real CPU usage to the OpenTAKServer dashboard.

Stock OTS 1.7.13 + UI 1.7.5 show no per-service status, and the CPU gauge
measures only the web process (and reads 0% on first load). This:

  1. copies service_status.py into the OTS package as cbu_service_status.py
  2. patches /api/status (blueprints/ots_api/api.py) to report whole-machine
     CPU and a "services" list, and adds POST /api/cbu/services/<action>
     (administrator only) to start/stop/restart one of those services
  3. patches the dashboard bundle to render that list with Start/Stop/Restart
     buttons for administrators, refreshing every 5s

The buttons also need the polkit rule in cbu-service-control.rules (see that
file), since OpenTAKServer runs unprivileged.

Re-run after upgrading OpenTAKServer or its UI (pip/zip installs overwrite
these files), or after editing this script: patched files are re-patched from
their *.orig, so the result is always this script's current version. If an
upgrade changed the code around a patch point, nothing is written and the
script says which anchor went missing.

Then: sudo systemctl restart opentakserver, and hard-refresh the dashboard.

    ~/.opentakserver_venv/bin/python deploy/ots-status/apply.py
    ~/.opentakserver_venv/bin/python deploy/ots-status/apply.py --revert
"""

import glob
import os
import re
import shutil
import sys

import opentakserver

HERE = os.path.dirname(os.path.abspath(__file__))
OTS_PKG = os.path.dirname(opentakserver.__file__)
API_PY = os.path.join(OTS_PKG, "blueprints", "ots_api", "api.py")
MODULE_DST = os.path.join(OTS_PKG, "cbu_service_status.py")
UI_JS_DIR = "/var/www/html/opentakserver/assets/js"
UI_JS_GLOB = os.path.join(UI_JS_DIR, "Dashboard-*.js")
BUTTON_JS_GLOB = os.path.join(UI_JS_DIR, "Button-*.js")

API_MARKER = "cbu_service_status"
API_EDITS = [
    ("import psutil\n",
     "import psutil\n"
     "from flask_security import roles_required\n"
     "from opentakserver import cbu_service_status\n"),
    ('        "cpu_percent": p.cpu_percent(),\n',
     '        "cpu_percent": cbu_service_status.system_cpu_percent(),\n'
     '        "services": cbu_service_status.safe_collect(),\n'
     '        "services_control": current_user.has_role("administrator"),\n'),
]
API_APPEND = '''

@api_blueprint.route("/api/cbu/services/<action>", methods=["POST"])
@auth_required()
@roles_required("administrator")
def cbu_service_control(action):
    """Start, stop or restart a dashboard service (added by deploy/ots-status)

    :param action: start, stop or restart
    :json unit: systemd unit name from the dashboard's services list
    """
    unit = (request.get_json(silent=True) or {}).get("unit")
    try:
        cbu_service_status.control(unit, action)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error("Dashboard {} of {} failed: {}".format(action, unit, e))
        return jsonify({"success": False, "error": str(e)}), 500
    logger.info("{} requested {} of {} from the dashboard".format(current_user.username, action, unit))
    return jsonify({"success": True})
'''

# Both versions of the dashboard patch contain this, pristine bundles don't.
JS_MARKER = "__cbu"
JS_EDITS = [
    # state for the services list and whether this user may control them
    ("[k,A]=(0,Y.useState)({boot_time:``,uptime:0});",
     "[k,A]=(0,Y.useState)({boot_time:``,uptime:0}),[__svc,__setSvc]=(0,Y.useState)([]),[__ctl,__setCtl]=(0,Y.useState)(!1);"),
    # fetch on a 5s interval instead of once, and store services
    ("return(0,Y.useEffect)(()=>{i.get(a.status).then(e=>{e.status===200&&(",
     "return(0,Y.useEffect)(()=>{let __poll=()=>i.get(a.status).then(e=>{e.status===200&&(__setSvc(e.data.services||[]),__setCtl(!!e.data.services_control),"),
    ("g(e.data.os_release))}).catch(e=>{console.log(e)})},[]),",
     "g(e.data.os_release))}).catch(e=>{console.log(e)});__poll();let __timer=setInterval(__poll,5e3);return()=>clearInterval(__timer)},[]),"),
    # render the table right under the "Server Status" heading
    ("children:`Server Status`})}),",
     "children:`Server Status`})}),(0,G.jsx)(__cbuServices,{v:__svc,ctl:__ctl,set:__setSvc}),"),
]
# Prepended: Mantine's Button, imported from its own chunk ({button} is filled
# in with that chunk's hashed filename).
JS_PREPEND = 'import{t as __cbuButton}from"./{button}";'
# Appended at module scope. Y=React, G=jsx runtime, n=Paper, r=Box, l=Title,
# s=Text, W=bytes formatter, i=axios instance (sends the XSRF token), a=API
# routes, all already bound in this bundle.
JS_APPEND = r"""
function __cbuUp(t){if(t==null)return`—`;let d=Math.floor(t/86400),h=Math.floor(t%86400/3600),m=Math.floor(t%3600/60);return d?`${d}d ${h}h`:h?`${h}h ${m}m`:`${m}m ${t%60}s`}
function __cbuServices({v,ctl,set}){let[busy,setBusy]=(0,Y.useState)({}),[err,setErr]=(0,Y.useState)(``);if(!v||!v.length)return null;
let act=(e,x)=>{if(x!==`start`&&!window.confirm(`${x===`stop`?`Stop`:`Restart`} ${e.name}?${e.note?`\n\n${e.note}`:``}`))return;setErr(``);setBusy(b=>({...b,[e.unit]:x}));i.post(`/api/cbu/services/${x}`,{unit:e.unit}).then(()=>{setTimeout(()=>i.get(a.status).then(r=>{r.status===200&&set(r.data.services||[])}).catch(()=>{}),1500)}).catch(r=>setErr(`Couldn't ${x} ${e.name}: ${r.response&&r.response.data&&r.response.data.error||r.message}`)).finally(()=>setBusy(b=>{let c={...b};delete c[e.unit];return c}))};
let btn=(e,x)=>(0,G.jsx)(__cbuButton,{size:`compact-xs`,variant:`light`,mr:6,color:x===`stop`?`red`:x===`start`?`green`:`blue`,disabled:x===`start`&&e.running||x===`stop`&&!e.running||!!busy[e.unit]&&busy[e.unit]!==x,loading:busy[e.unit]===x,onClick:()=>act(e,x),children:x[0].toUpperCase()+x.slice(1)},x);
let up=v.filter(e=>e.running).length,b=`1px solid var(--mantine-color-default-border)`,th={textAlign:`left`,padding:`6px 12px`,borderBottom:b,whiteSpace:`nowrap`},td={padding:`6px 12px`,borderBottom:b,whiteSpace:`nowrap`,verticalAlign:`top`},cols=[`Service`,`Status`,`Uptime`,`CPU`,`Memory`,`Ports`,`Restarts`].concat(ctl?[`Control`]:[]);return(0,G.jsx)(r,{mb:`xl`,children:(0,G.jsxs)(n,{withBorder:!0,shadow:`xl`,radius:`md`,p:`xl`,children:[(0,G.jsx)(r,{mb:`md`,children:(0,G.jsx)(l,{order:4,c:up===v.length?void 0:`red`,children:`Services: ${up} of ${v.length} running`})}),err&&(0,G.jsx)(s,{c:`red`,size:`sm`,mb:`md`,children:err}),(0,G.jsx)(`div`,{style:{overflowX:`auto`},children:(0,G.jsxs)(`table`,{style:{borderCollapse:`collapse`,width:`100%`},children:[(0,G.jsx)(`thead`,{children:(0,G.jsx)(`tr`,{children:cols.map(e=>(0,G.jsx)(`th`,{style:th,children:e},e))})}),(0,G.jsx)(`tbody`,{children:v.map(e=>(0,G.jsxs)(`tr`,{children:[(0,G.jsxs)(`td`,{style:td,children:[(0,G.jsx)(s,{fw:700,size:`sm`,children:e.name}),(0,G.jsx)(s,{size:`xs`,c:`dimmed`,children:e.unit})]}),(0,G.jsx)(`td`,{style:td,children:(0,G.jsx)(s,{size:`sm`,fw:700,c:e.running?`green`:`red`,children:e.running?`● Running`:`● ${e.active_state}${e.sub_state?` (${e.sub_state})`:``}`})}),(0,G.jsx)(`td`,{style:td,children:__cbuUp(e.uptime)}),(0,G.jsx)(`td`,{style:td,children:e.cpu_percent==null?`…`:`${e.cpu_percent}%`}),(0,G.jsx)(`td`,{style:td,children:e.memory==null?`—`:W(e.memory)}),(0,G.jsx)(`td`,{style:td,children:e.ports.length?e.ports.map(p=>(0,G.jsx)(s,{span:!0,size:`sm`,mr:`xs`,c:p.listening?void 0:`red`,children:p.listening?`${p.port}`:`${p.port} closed`},p.port)):`—`}),(0,G.jsx)(`td`,{style:td,children:e.restarts??`—`}),ctl&&(0,G.jsx)(`td`,{style:td,children:(e.actions||[]).map(x=>btn(e,x))})]},e.unit))})]})})]})})}
"""


def patch(path, marker, edits, append="", prepend=""):
    # newline="" keeps the file's own line endings (OTS ships api.py as CRLF)
    with open(path, newline="") as f:
        current = f.read()
    orig = path + ".orig"
    if marker in current:
        # Patched by an earlier run: start over from the pristine copy.
        if not os.path.exists(orig):
            sys.exit("ABORT: {} is patched but {} is missing. Reinstall that file "
                     "and re-run. Nothing written.".format(path, orig))
        with open(orig, newline="") as f:
            src = f.read()
    else:
        src = current
    if "\r\n" in src:
        edits = [(o.replace("\n", "\r\n"), n.replace("\n", "\r\n")) for o, n in edits]
        append = append.replace("\n", "\r\n")
    out = src
    for old, new in edits:
        if out.count(old) != 1:
            sys.exit("ABORT: patch anchor not found exactly once in {}:\n  {!r}\n"
                     "OTS/UI was probably upgraded; update apply.py. Nothing written."
                     .format(path, old[:80]))
        out = out.replace(old, new)
    out = prepend + out + append
    if out == current:
        print("up to date:", path)
        return
    if marker not in current:
        # Fresh install or upgrade: this is the pristine file.
        shutil.copy2(path, orig)
    with open(path, "w", newline="") as f:
        f.write(out)
    print("patched:", path)


def revert(path):
    if os.path.exists(path + ".orig"):
        shutil.copy2(path + ".orig", path)
        os.remove(path + ".orig")
        print("reverted:", path)


def find_button_chunk():
    # The chunk whose `t` export is Mantine's Button.
    for path in glob.glob(BUTTON_JS_GLOB):
        if path.endswith(".orig"):
            continue
        with open(path) as f:
            src = f.read()
        m = re.search(r"(\w+)\.displayName=`@mantine/core/Button`", src)
        if m and re.search(r"export\{[^}]*\b" + re.escape(m.group(1)) + r" as t\b", src):
            return os.path.basename(path)
    sys.exit("ABORT: no {} exports Mantine's Button as `t`. UI was probably "
             "upgraded; update apply.py. Nothing written.".format(BUTTON_JS_GLOB))


def main():
    js = glob.glob(UI_JS_GLOB)
    js = [p for p in js if not p.endswith(".orig")]
    if len(js) != 1:
        sys.exit("ABORT: expected one dashboard bundle matching {}, found {}".format(UI_JS_GLOB, js))

    if "--revert" in sys.argv:
        revert(API_PY)
        revert(js[0])
        if os.path.exists(MODULE_DST):
            os.remove(MODULE_DST)
        print("Now: sudo systemctl restart opentakserver")
        return

    js_prepend = JS_PREPEND.replace("{button}", find_button_chunk())
    shutil.copy2(os.path.join(HERE, "service_status.py"), MODULE_DST)
    print("installed:", MODULE_DST)
    patch(API_PY, API_MARKER, API_EDITS, API_APPEND)
    patch(js[0], JS_MARKER, JS_EDITS, JS_APPEND, js_prepend)
    if not os.path.exists("/etc/polkit-1/rules.d/50-cbu-service-control.rules"):
        print("For the Start/Stop/Restart buttons, also install the polkit rule:\n"
              "  sudo install -m 644 {} /etc/polkit-1/rules.d/50-cbu-service-control.rules"
              .format(os.path.join(HERE, "cbu-service-control.rules")))
    print("Now: sudo systemctl restart opentakserver, then hard-refresh the dashboard (Ctrl+Shift+R)")


if __name__ == "__main__":
    main()
