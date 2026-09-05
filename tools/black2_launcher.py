#!/usr/bin/env python3
"""One-click local supervisor for the Pokémon Black 2 runtime workbench."""
from __future__ import annotations

import argparse, json, os, subprocess, sys, time, urllib.request, urllib.error, webbrowser
from pathlib import Path
from datetime import datetime
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime'; CONFIG=RUNTIME/'runtime.local.json'; STATE=RUNTIME/'launcher_state.json'; LOG=ROOT/'logs'/'launcher.log'


def _load(path:Path)->dict[str,Any]:
    try:
        v=json.loads(path.read_text(encoding='utf-8')); return v if isinstance(v,dict) else {}
    except (OSError,ValueError): return {}

def _save(path:Path,data:dict[str,Any])->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

def _log(msg:str)->None:
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a',encoding='utf-8') as f:f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")

def cfg()->dict[str,Any]:
    c=_load(CONFIG); c.setdefault('http_host','127.0.0.1'); c.setdefault('http_port',8765); return c

def url(c):return f"http://{c['http_host']}:{int(c['http_port'])}"
def health(c):
    try:
        with urllib.request.urlopen(url(c)+'/health',timeout=1.3) as r:return json.loads(r.read().decode())
    except Exception:return None

def bridge(c):
    try:
        with urllib.request.urlopen(url(c)+'/api/bizhawk/status',timeout=1.3) as r:return bool(json.loads(r.read().decode()).get('connected'))
    except Exception:return False

def pid_alive(pid:int|None)->bool:
    if not pid:return False
    if os.name=='nt':
        r=subprocess.run(['tasklist','/FI',f'PID eq {pid}','/FO','CSV','/NH'],capture_output=True,text=True);return str(pid) in r.stdout
    try:os.kill(pid,0);return True
    except OSError:return False

def backend_listener_pid(c:dict[str,Any])->int|None:
    """Find the local HTTP listener, including a restarted backend child."""
    if os.name!='nt':return None
    port=str(int(c['http_port']))
    try:
        result=subprocess.run(['netstat','-ano','-p','TCP'],capture_output=True,text=True,timeout=3,check=False)
        for line in result.stdout.splitlines():
            fields=line.split()
            if len(fields)>=5 and fields[-2].upper()=='LISTENING' and fields[-1].isdigit():
                local=fields[1]
                if local.endswith(':'+port) and local.rsplit(':',1)[0] in {'127.0.0.1','[::1]','0.0.0.0','[::]'}:
                    return int(fields[-1])
    except (OSError,subprocess.SubprocessError):
        pass
    return None

def ensure_env()->Path:
    target=ROOT/'.venv'/'Scripts'/'python.exe'
    created=not target.is_file()
    if created:
        _log('creating local .venv')
        subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],cwd=ROOT,check=True)
    if not target.is_file(): raise RuntimeError('创建 .venv 失败')
    req=ROOT/'requirements.txt'
    pip_ready=subprocess.run([str(target),'-m','pip','--version'],cwd=ROOT,capture_output=True).returncode==0
    if not pip_ready:
        _log('bootstrapping local pip')
        subprocess.run([str(target),'-m','ensurepip','--upgrade'],cwd=ROOT,check=True)
    if req.is_file() and (created or not pip_ready):
        _log('installing requirements')
        subprocess.run([str(target),'-m','pip','install','-r',str(req)],cwd=ROOT,check=True)
    return target

def pyexe()->Path:
    p=ROOT/'.venv'/'Scripts'/'python.exe';return p if p.is_file() else Path(sys.executable)

def validate(c):
    out=[];biz=Path(str(c.get('bizhawk_path') or ''));rom=Path(str(c.get('rom_path') or ''))
    if not biz.is_file() or biz.name.lower()!='emuhawk.exe':out.append('请选择 BizHawk 的 EmuHawk.exe')
    if not rom.is_file() or rom.suffix.lower()!='.nds':out.append('请选择你合法持有的 .nds ROM')
    if not (ROOT/'bridge'/'bizhawk'/'black2_bridge.lua').is_file():out.append('找不到 black2_bridge.lua')
    return out

def env_for(c):
    e=os.environ.copy();e['BLACK2_ROM_PATH']=str(Path(c['rom_path']).resolve());e['BLACK2_BIZHAWK_DIR']=str(Path(c['bizhawk_path']).resolve().parent);e['BLACK2_PROJECT_ROOT']=str(ROOT);e.setdefault('BLACK2_ENABLE_LEGACY_MAP_CACHE','0');return e

def wait(fn,seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn():return True
        time.sleep(.25)
    return False

def start(open_browser=True):
    c=cfg();problems=validate(c)
    if problems:raise RuntimeError('；'.join(problems))
    c['bizhawk_path']=str(Path(c['bizhawk_path']).resolve());c['rom_path']=str(Path(c['rom_path']).resolve());_save(CONFIG,c)
    st=_load(STATE);env=env_for(c)
    if not health(c):
        logs=(ROOT/'logs');logs.mkdir(parents=True,exist_ok=True);f=(logs/'runtime-supervisor.log').open('a',encoding='utf-8')
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        runtime_python=ensure_env()
        p=subprocess.Popen([str(runtime_python),str(ROOT/'run_runtime.py')],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,creationflags=flags);st['backend_pid']=p.pid;_log(f'backend {p.pid}')
        if not wait(lambda:bool(health(c)),15):raise RuntimeError('后端启动失败，请查看 logs/runtime-supervisor.log')
    if not bridge(c):
        p=subprocess.Popen([c['bizhawk_path'],f"--lua={(ROOT/'bridge'/'bizhawk'/'black2_bridge.lua').resolve()}",c['rom_path']],cwd=Path(c['bizhawk_path']).parent,env=env);st['emuhawk_pid']=p.pid;_log(f'EmuHawk {p.pid}');wait(lambda:bridge(c),12)
    st.update({'updated_at':datetime.now().isoformat(timespec='seconds'),'rom_path':c['rom_path'],'bizhawk_path':c['bizhawk_path']});_save(STATE,st)
    if open_browser:webbrowser.open(url(c)+'/')
    return status()

def stop():
    c=cfg();st=_load(STATE)
    pid=backend_listener_pid(c) or int(st.get('backend_pid') or 0)
    if pid_alive(pid):
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(pid),'/T'],capture_output=True,text=True,timeout=8)
            _log(f'backend stop requested pid={pid}')
        else:os.kill(pid,15)
    st.pop('backend_pid',None)
    emu=int(st.get('emuhawk_pid') or 0)
    if emu and pid_alive(emu) and os.name=='nt':
        # Graceful close only; never force-kill emulator state.
        try:
            import ctypes
            from ctypes import wintypes
            @ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
            def cb(hwnd,_):
                p=wintypes.DWORD();ctypes.windll.user32.GetWindowThreadProcessId(hwnd,ctypes.byref(p))
                if p.value==emu and ctypes.windll.user32.IsWindowVisible(hwnd):ctypes.windll.user32.PostMessageW(hwnd,0x0010,0,0)
                return True
            ctypes.windll.user32.EnumWindows(cb,0)
        except Exception:pass
    _save(STATE,st);return status()

def status():
    c=cfg();st=_load(STATE);return {'backend_online':bool(health(c)),'bridge_connected':bridge(c),'backend_pid':st.get('backend_pid'),'emuhawk_pid':st.get('emuhawk_pid'),'rom_configured':Path(str(c.get('rom_path') or '')).is_file(),'bizhawk_configured':Path(str(c.get('bizhawk_path') or '')).is_file(),'url':url(c)}

class WindowsTray:
    """Small dependency-free Windows notification-area menu for the Tk launcher."""
    CALLBACK=0x8000+20; WM_LBUTTONUP=0x0202; WM_RBUTTONUP=0x0205
    NIM_ADD=0; NIM_DELETE=2; NIF_MESSAGE=1; NIF_ICON=2; NIF_TIP=4

    def __init__(self,root,show,quit_):
        import ctypes
        from ctypes import wintypes
        self.ctypes=ctypes;self.root=root;self.show=show;self.quit=quit_;self.visible=False
        self.user32=ctypes.windll.user32;self.shell32=ctypes.windll.shell32;self.hwnd=root.winfo_id()
        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('hWnd',wintypes.HWND),('uID',wintypes.UINT),('uFlags',wintypes.UINT),('uCallbackMessage',wintypes.UINT),('hIcon',wintypes.HANDLE),('szTip',wintypes.WCHAR*128),('dwState',wintypes.DWORD),('dwStateMask',wintypes.DWORD),('szInfo',wintypes.WCHAR*256),('uTimeoutOrVersion',wintypes.UINT),('szInfoTitle',wintypes.WCHAR*64),('dwInfoFlags',wintypes.DWORD),('guidItem',ctypes.c_byte*16),('hBalloonIcon',wintypes.HANDLE)]
        self.data=NOTIFYICONDATAW();self.data.cbSize=ctypes.sizeof(NOTIFYICONDATAW);self.data.hWnd=self.hwnd;self.data.uID=1;self.data.uFlags=self.NIF_MESSAGE|self.NIF_ICON|self.NIF_TIP;self.data.uCallbackMessage=self.CALLBACK;self.data.hIcon=self.user32.LoadIconW(None,32512);self.data.szTip='Pokémon Black 2 Launcher'
        PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,wintypes.HWND,wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t)
        set_proc=self.user32.SetWindowLongPtrW;set_proc.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_void_p];set_proc.restype=ctypes.c_void_p
        call_proc=self.user32.CallWindowProcW;call_proc.argtypes=[ctypes.c_void_p,wintypes.HWND,wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t];call_proc.restype=ctypes.c_ssize_t
        self.old_proc=None
        @PROC
        def wndproc(hwnd,msg,wparam,lparam):
            if msg==self.CALLBACK:
                if lparam==self.WM_LBUTTONUP:root.after(0,self.show);return 0
                if lparam==self.WM_RBUTTONUP:root.after(0,self.menu);return 0
            return call_proc(self.old_proc,hwnd,msg,wparam,lparam)
        self.wndproc=wndproc;self.old_proc=set_proc(self.hwnd,-4,ctypes.cast(wndproc,ctypes.c_void_p).value)

    def show_icon(self):
        if not self.visible:self.shell32.Shell_NotifyIconW(self.NIM_ADD,self.ctypes.byref(self.data));self.visible=True

    def hide_icon(self):
        if self.visible:self.shell32.Shell_NotifyIconW(self.NIM_DELETE,self.ctypes.byref(self.data));self.visible=False

    def menu(self):
        from ctypes import wintypes
        point=wintypes.POINT();self.user32.GetCursorPos(self.ctypes.byref(point));menu=self.user32.CreatePopupMenu();self.user32.AppendMenuW(menu,0,1,'显示启动器');self.user32.AppendMenuW(menu,0x800,0,None);self.user32.AppendMenuW(menu,0,2,'退出并停止后端');self.user32.SetForegroundWindow(self.hwnd);choice=self.user32.TrackPopupMenu(menu,0x102,point.x,point.y,0,self.hwnd,None);self.user32.DestroyMenu(menu)
        if choice==1:self.show()
        elif choice==2:self.quit()

def gui():
    ensure_env()
    import tkinter as tk
    from tkinter import filedialog,messagebox
    root=tk.Tk();root.title('Pokémon Black 2 Workbench · 一键启动器');root.geometry('720x460');c=cfg();biz=tk.StringVar(value=str(c.get('bizhawk_path') or ''));rom=tk.StringVar(value=str(c.get('rom_path') or ''));st=tk.StringVar(value='');exiting=False
    tray:WindowsTray|None=None
    def show_window():
        if tray is not None:tray.hide_icon()
        root.deiconify();root.lift();root.focus_force()
    def exit_and_stop():
        nonlocal exiting
        if exiting:return
        exiting=True
        try:stop()
        except Exception as error:_log(f'backend stop failed on launcher exit: {error!r}')
        if tray is not None:tray.hide_icon()
        root.destroy()
    if os.name=='nt':tray=WindowsTray(root,show_window,exit_and_stop)
    def minimize_to_tray():
        root.withdraw()
        if tray is not None:tray.show_icon()
    def save():cc=cfg();cc['bizhawk_path']=biz.get().strip();cc['rom_path']=rom.get().strip();_save(CONFIG,cc)
    def choose_biz():
        p=filedialog.askopenfilename(title='选择 EmuHawk.exe',filetypes=[('EmuHawk','EmuHawk.exe'),('EXE','*.exe')]);
        if p:biz.set(p);save()
    def choose_rom():
        p=filedialog.askopenfilename(title='选择 .nds ROM',filetypes=[('Nintendo DS ROM','*.nds')]);
        if p:rom.set(p);save()
    def refresh():
        s=status();st.set(f"后端 {'在线' if s['backend_online'] else '未启动'}   ·   Bridge {'已连接' if s['bridge_connected'] else '未连接'}")
        root.after(1500,refresh)
    def go():
        try:save();start(True)
        except Exception as e:messagebox.showerror('启动失败',str(e))
    def halt():
        try:stop()
        except Exception as e:messagebox.showerror('停止失败',str(e))
    tk.Label(root,text='Pokémon Black 2 Runtime Workbench',font=('Segoe UI',18,'bold')).pack(anchor='w',padx=20,pady=(18,4));tk.Label(root,text='第一次选择 BizHawk 和 ROM，之后只点“一键启动”。关闭窗口会最小化到通知区；右键图标可“退出并停止后端”。',fg='#555').pack(anchor='w',padx=20,pady=(0,14));f=tk.Frame(root);f.pack(fill='x',padx=20);f.columnconfigure(1,weight=1)
    for row,(label,var,cmd) in enumerate([('BizHawk',biz,choose_biz),('NDS ROM',rom,choose_rom)]):tk.Label(f,text=label,width=10,anchor='w').grid(row=row,column=0,padx=6,pady=8);tk.Entry(f,textvariable=var).grid(row=row,column=1,sticky='ew',padx=6,pady=8);tk.Button(f,text='选择…',command=cmd).grid(row=row,column=2,padx=6,pady=8)
    tk.Label(root,textvariable=st,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=26,pady=12);a=tk.Frame(root);a.pack(padx=20,pady=8);tk.Button(a,text='▶ 一键启动',width=18,height=2,command=go).grid(row=0,column=0,padx=6);tk.Button(a,text='■ 安全停止',width=18,height=2,command=halt).grid(row=0,column=1,padx=6);tk.Button(a,text='打开 3D Workbench',width=18,height=2,command=lambda:webbrowser.open(url(cfg())+'/#world')).grid(row=1,column=0,padx=6,pady=8);tk.Button(a,text='运行时监控',width=18,height=2,command=lambda:webbrowser.open(url(cfg())+'/#monitor')).grid(row=1,column=1,padx=6,pady=8);root.protocol('WM_DELETE_WINDOW',minimize_to_tray);refresh();root.mainloop()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('cmd',nargs='?',default='gui',choices=['gui','start','stop','status']);ap.add_argument('--no-browser',action='store_true');a=ap.parse_args()
    if a.cmd=='gui':gui();return 0
    print(json.dumps(start(not a.no_browser) if a.cmd=='start' else stop() if a.cmd=='stop' else status(),ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
