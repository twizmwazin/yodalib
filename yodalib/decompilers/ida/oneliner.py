import sys
import os
import subprocess
from urllib.request import urlretrieve

# How to run:
# import urllib.request, sys, os; sys.path.insert(1, os.getcwd()); urllib.request.urlretrieve("https://raw.githubusercontent.com/angr/yodalib/one_liner_ida/plugins/ida_yodalib/ida_yodalib/oneliner.py", "oneliner.py"); from oneliner import install; install()


class PlatformType:
    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"


def find_platform():
    platform = PlatformType.WINDOWS

    if sys.platform == "linux" or sys.platform == "linux2":
        platform = PlatformType.LINUX
    elif sys.platform == "darwin":
        platform = PlatformType.MACOS

    return platform


def plugin_install_yodalib(plugins_path):
    github_url_base = "https://raw.githubusercontent.com/angr/yodalib/master/plugins/ida_yodalib/"
    ida_yodalib_folder = os.path.join(plugins_path, "ida_yodalib")

    # install entry point of yodalib
    urlretrieve(github_url_base+"ida_yodalib.py", os.path.join(plugins_path, "ida_yodalib.py"))

    # install ida_yodalib/*
    github_url_base += "ida_yodalib/"
    files_to_download = ["__init__.py", "compat.py", "interface.py", "hooks.py", "plugin.py", "artifact_lifter.py"]
    try:
        os.mkdir(ida_yodalib_folder)
    except FileExistsError:
        pass
    for f in files_to_download:
        urlretrieve(github_url_base+f, os.path.join(ida_yodalib_folder, f))


def pip_install_yodalib(python_path):
    subprocess.run([python_path] + "-m pip install yodalib".split(" "))
    # just in case...
    location = subprocess.run(["which", "python3"], stdout=subprocess.PIPE)
    python_path = location.stdout.strip()
    subprocess.run([python_path.decode()] + "-m pip install git+https://github.com/angr/yodalib".split(" "))


def install():
    # confirm install platform works
    platform = find_platform()
    if platform not in [PlatformType.LINUX, PlatformType.MACOS]:
        os.remove("oneliner.py")
        raise Exception("Platform is not supported for oneliner install.")

    # find python executable
    if platform == PlatformType.LINUX:
        python_path = sys.executable
    elif platform == PlatformType.MACOS:
        for lib_path in sys.path:
            basename = os.path.basename(lib_path)
            if basename.startswith("python") and not basename.endswith(".zip"):
                python_path = os.path.join(lib_path, f"../../bin/{basename}")
                if os.path.exists(python_path):
                    break
        else:
            os.remove("oneliner.py")
            raise Exception("Unable to locate your local python executable. Please use manual install.")

    pip_install_yodalib(python_path)
    print("[+] Successfully installed yodalib to IDA pip")

    # find plugin path
    not_found = False
    for plugin_path in sys.path:
        if os.path.basename(plugin_path) == "plugins" and "ida" in plugin_path:
            break
    else:
        not_found = True

    # tray again with a less reliable search
    if not_found:
        for plugin_path in sys.path:
            if os.path.basename(plugin_path) == "python" and "ida" in plugin_path:
                break
        else:
            os.remove("oneliner.py")
            raise Exception("Unable to find the local plugins folder to install yodalib, install manually!")

        plugin_path = os.path.join(os.path.dirname(plugin_path), "plugins")

    plugin_install_yodalib(plugin_path)
    print("[+] Successfully installed yodalib IDA Plugin into the plugins folder")
    print("[+] Install finished. PLEASE RESTART IDA for plugin to be loaded")
    os.remove("oneliner.py")
