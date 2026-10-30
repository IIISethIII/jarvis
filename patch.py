import os
import subprocess

inject_py = """
import gc, multiprocessing, sys
try:
    patched = False
    for obj in gc.get_objects():
        if type(obj).__name__ == 'AudioManager':
            if getattr(obj, 'audio_proc', None):
                try:
                    obj.audio_proc.terminate()
                except:
                    pass
            obj.audio_queue = multiprocessing.Queue()
            patched = True
            with open('/tmp/gdb_out.log', 'w') as f:
                f.write('Found AudioManager and patched queue!\\n')
            break
    if not patched:
        with open('/tmp/gdb_out.log', 'w') as f:
            f.write('AudioManager not found in GC\\n')
except Exception as e:
    with open('/tmp/gdb_out.log', 'w') as f:
        f.write('Error: ' + str(e) + '\\n')
"""

with open("/tmp/inject.py", "w") as f:
    f.write(inject_py)

cmd = ['sudo', 'gdb', '-p', '3993', '-batch', '-ex', 'call (void)PyRun_SimpleString("with open(\\"/tmp/inject.py\\") as f: exec(f.read())")']
print("Running GDB...")
subprocess.run(cmd)

if os.path.exists('/tmp/gdb_out.log'):
    with open('/tmp/gdb_out.log', 'r') as f:
        print(f.read())
