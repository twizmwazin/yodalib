import inspect
import logging
import threading
from collections import defaultdict
from functools import wraps
from typing import Dict, Optional, Union, Tuple

import yodalib
from yodalib.api.artifact_lifter import ArtifactLifter
from yodalib.api.artifact_dict import ArtifactDict
from yodalib.api.type_parser import CTypeParser, CType
from yodalib.data import (
    State, Artifact,
    Function, FunctionHeader, StackVariable,
    Comment, GlobalVariable, Patch,
    Enum, Struct
)
from yodalib.decompilers import YODALIB_SUPPORTED_DECOMPILERS, ANGR_DECOMPILER, \
    BINJA_DECOMPILER, IDA_DECOMPILER, GHIDRA_DECOMPILER

_l = logging.getLogger(name=__name__)


def artifact_set_event(f):
    @wraps(f)
    def _artifact_set_event(self: "DecompilerInterface", *args, **kwargs):
        return self.artifact_set_event_handler(f, *args, **kwargs)

    return _artifact_set_event


class DummyArtifactSetLock:
    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class DecompilerInterface:
    def __init__(self, artifact_lifter: Optional[ArtifactLifter] = None, headless: bool = False, error_on_artifact_duplicates=False):
        self.headless = headless
        self.artifact_lifer = artifact_lifter
        self.type_parser = CTypeParser()
        self._error_on_artifact_duplicates = True

        self.artifact_set_lock = threading.Lock()

        # artifacts
        self.functions = ArtifactDict(Function, self, error_on_duplicate=error_on_artifact_duplicates)
        self.stack_vars = ArtifactDict(StackVariable, self, error_on_duplicate=error_on_artifact_duplicates)
        self.comments = ArtifactDict(Comment, self, error_on_duplicate=error_on_artifact_duplicates)
        self.enums = ArtifactDict(Enum, self, error_on_duplicate=error_on_artifact_duplicates)
        self.structs = ArtifactDict(Struct, self, error_on_duplicate=error_on_artifact_duplicates)
        self.patches = ArtifactDict(Patch, self, error_on_duplicate=error_on_artifact_duplicates)

        if not self.headless:
            self._init_ui_components()

    #
    # Decompiler GUI API
    #

    def _init_ui_components(self):
        pass

    def active_context(self) -> yodalib.data.Function:
        """
        Returns an yodalib Function. Currently only functions are supported as current contexts.
        This function will be called very frequently, so its important that its implementation is fast
        and can be done many times in the decompiler.
        """
        raise NotImplementedError

    def goto_address(self, func_addr) -> None:
        """
        Relocates decompiler display to provided address

        @param func_addr:
        @return:
        """
        raise NotImplementedError

    #
    # Override Mandatory API:
    # These functions create a public API for things that hold a reference to the Controller from either another
    # thread or object. This is most useful for use in the UI, which can use this API to make general requests from
    # the decompiler regardless of internal decompiler API.
    #

    @property
    def binary_hash(self) -> str:
        """
        Returns a hex string of the currently loaded binary in the decompiler. For most cases,
        this will simply be a md5hash of the binary.

        @rtype: hex string
        """
        raise NotImplementedError

    @property
    def binary_path(self) -> Optional[str]:
        """
        Returns a string that is the path of the currently loaded binary. If there is no binary loaded
        then None should be returned.

        @rtype: path-like string (/path/to/binary)
        """
        raise NotImplementedError

    def get_func_size(self, func_addr) -> int:
        """
        Returns the size of a function

        @param func_addr:
        @return:
        """
        raise NotImplementedError

    @property
    def decompiler_available(self) -> bool:
        """
        @return: True if decompiler is available for decompilation, False if otherwise
        """
        return True

    def decompile(self, addr: int) -> Optional[str]:
        if not self.decompiler_available:
            return None

        # TODO: make this a function call after transitioning decompiler artifacts to LiveState
        for search_addr in (addr, self.artifact_lifer.lower_addr(addr)):
            func_found = False
            for func_addr, func in self._functions().items():
                if func.addr <= search_addr < (func.addr + func.size):
                    func_found = True
                    break
            else:
                func = None

            if func_found:
                break
        else:
            return None

        try:
            decompilation = self._decompile(func)
        except Exception as e:
            _l.warning(f"Failed to decompile function at {hex(addr)}: {e}")
            decompilation = None

        return decompilation

    def _decompile(self, function: Function) -> Optional[str]:
        raise NotImplementedError

    #
    # Optional Artifact API:
    # A series of functions that allow public access to live artifacts in the decompiler. As an example,
    # `function(addr)` will return the current Function at addr that the user would be seeing. This is useful
    # for having a common interface of reading data from other decompilers.
    #

    # functions
    def _set_function(self, func: Function, **kwargs) -> bool:
        update = False
        header = func.header
        if header is not None:
            update = self._set_function_header(header, **kwargs)

        if func.stack_vars:
            for variable in func.stack_vars.values():
                update |= self._set_stack_variable(variable, **kwargs)

        return update

    def _get_function(self, addr, **kwargs) -> Optional[Function]:
        return None

    def _functions(self) -> Dict[int, Function]:
        """
        Returns a dict of yodalib.Functions that contain the addr, name, and size of each function in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    # stack vars
    def _set_stack_variable(self, svar: StackVariable, **kwargs) -> bool:
        return False

    def _get_stack_variable(self, addr: int, offset: int, **kwargs) -> Optional[StackVariable]:
        func = self._get_function(addr, **kwargs)
        if func is None:
            return None

        return func.stack_vars.get(offset, None)

    def _stack_variables(self, **kwargs) -> Dict[Tuple[int, int], StackVariable]:
        stack_vars = defaultdict(dict)
        for addr in self._functions():
            func = self._get_function(addr, **kwargs)
            for svar in func.stack_vars.values():
                stack_vars[addr][svar.offset] = svar

        return dict(stack_vars)

    # global variables
    def _set_global_variable(self, gvar: GlobalVariable, **kwargs) -> bool:
        return False

    def _get_global_var(self, addr) -> Optional[GlobalVariable]:
        return None

    def _global_vars(self) -> Dict[int, GlobalVariable]:
        """
        Returns a dict of yodalib.GlobalVariable that contain the addr and size of each global var.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    # structs
    def _set_struct(self, struct: Struct, header=True, members=True, **kwargs) -> bool:
        return False

    def _get_struct(self, name) -> Optional[Struct]:
        return None

    def _structs(self) -> Dict[str, Struct]:
        """
        Returns a dict of yodalib.Structs that contain the name and size of each struct in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    # enums
    def _set_enum(self, enum: Enum, **kwargs) -> bool:
        return False

    def _get_enum(self, name) -> Optional[Enum]:
        return None

    def _enums(self) -> Dict[str, Enum]:
        """
        Returns a dict of yodalib.Enum that contain the name of the enums in the decompiler.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    # patches
    def _set_patch(self, patch: Patch, **kwargs) -> bool:
        return False

    def _get_patch(self, addr) -> Optional[Patch]:
        return None

    def _patches(self) -> Dict[int, Patch]:
        """
        Returns a dict of yodalib.Patch that contain the addr of each Patch and the bytes.
        Note: this does not contain the live data of the Artifact, only the minimum knowledge to that the Artifact
        exists. To get live data, use the singleton function of the same name.

        @return:
        """
        return {}

    # comments
    def _set_comment(self, comment: Comment, **kwargs) -> bool:
        return False

    def _get_comment(self, addr) -> Optional[Comment]:
        return None

    def _comments(self) -> Dict[int, Comment]:
        return {}

    # others...
    def _set_function_header(self, fheader: FunctionHeader, **kwargs) -> bool:
        return False


    #
    # special
    #

    def global_artifacts(self):
        """
        Returns a light version of all artifacts that are global (non function associated):
        - structs, gvars, enums

        @return:
        """
        g_artifacts = {}
        for f in [self._structs, self._global_vars, self._enums]:
            g_artifacts.update(f())

        return g_artifacts

    def global_artifact(self, lookup_item: Union[str, int]):
        """
        Returns a live yodalib.data version of the Artifact located at the lookup_item location, which can
        lookup any artifact supported in `global_artifacts`

        @param lookup_item:
        @return:
        """

        if isinstance(lookup_item, int):
            return self._get_global_var(lookup_item)
        elif isinstance(lookup_item, str):
            artifact = self._get_struct(lookup_item)
            if artifact:
                return artifact

            artifact = self._get_enum(lookup_item)
            return artifact

        return None

    def set_artifact(self, artifact: Artifact, lower=True, **kwargs) -> bool:
        """
        Sets a yodalib Artifact into the decompilers local database. This operations allows you to change
        what the native decompiler sees with yodalib Artifacts. This is different from opertions on a yodalib State,
        since this is native to the decompiler

        >>> func = Function(0xdeadbeef, 0x800)
        >>> func.name = "main"
        >>> controller.set_artifact(func)

        @param artifact:
        @param lower:       Wether to convert the Artifacts types and offset into the local decompilers format
        @return:            True if the Artifact was succesfuly set into the decompiler
        """
        set_map = {
            Function: self._set_function,
            FunctionHeader: self._set_function_header,
            StackVariable: self._set_stack_variable,
            Comment: self._set_comment,
            GlobalVariable: self._set_global_variable,
            Struct: self._set_struct,
            Enum: self._set_enum,
            Patch: self._set_patch,
            Artifact: None,
        }

        if lower:
            artifact = self.lower_artifact(artifact)

        setter = set_map.get(type(artifact), None)
        if setter is None:
            _l.critical(f"Unsupported object is attempting to be set, please check your object: {artifact}")
            return False

        return setter(artifact, **kwargs)

    #
    # Change Callback API
    # TODO: all the code in this category on_* is experimental and not ready for production use
    # all this code should be implemented in the other decompilers or moved to a different project
    #

    def on_function_header_changed(self, fheader: FunctionHeader):
        pass

    def on_stack_variable_changed(self, svar: StackVariable):
        pass

    def on_comment_changed(self, comment: Comment):
        pass

    def on_struct_changed(self, struct: Struct):
        pass

    def on_enum_changed(self, enum: Enum):
        pass

    def on_global_variable_changed(self, gvar: GlobalVariable):
        pass

    #
    # Client API & Shortcuts
    #

    def lift_artifact(self, artifact: Artifact) -> Artifact:
        return self.artifact_lifer.lift(artifact)

    def lower_artifact(self, artifact: Artifact) -> Artifact:
        return self.artifact_lifer.lower(artifact)

    #
    # Fillers:
    # A filler function is generally responsible for pulling down data from a specific user state
    # and reflecting those changes in decompiler view (like the text on the screen). Normally, these changes
    # will also be accompanied by a Git commit to the master users state to save the changes from pull and
    # fill into their BS database. In special cases, a filler may only update the decompiler UI but not directly
    # cause a save of the BS state.
    #

    def artifact_set_event_handler(
        self, setter_func, artifact: Artifact, *args, **kwargs
    ):
        """
        This function handles any event which tries to set an Artifact into the decompiler. This handler does two
        important tasks:
        1. Locks callback handlers, so you don't get infinite callbacks
        2. "Lowers" the artifact, so it's data types match the decompilers

        Because of this, it's recommended that when overriding this function you always call super() at the end of
        your override so it's set correctly in the decompiler.

        :param setter_func:
        :param artifact:
        :param args:
        :param kwargs:
        :return:
        """

        lowered_artifact = self.lower_artifact(artifact)
        lock = self.artifact_set_lock if not self.artifact_set_lock.locked() else DummyArtifactSetLock()
        with lock:
            try:
                had_changes = setter_func(lowered_artifact, **kwargs)
            except ValueError:
                had_changes = False

        return had_changes

    #
    # Utils
    #

    def type_is_user_defined(self, type_str, state=None):
        if not type_str:
            return None

        type_: CType = self.type_parser.parse_type(type_str)
        if not type_:
            # it was not parseable
            return None

        # type is known and parseable
        if not type_.is_unknown:
            return None

        base_type_str = type_.base_type.type
        return base_type_str if base_type_str in state._structs.keys() else None

    @staticmethod
    def _find_global_in_call_frames(global_name, max_frames=10):
        curr_frame = inspect.currentframe()
        outer_frames = inspect.getouterframes(curr_frame, max_frames)
        for frame in outer_frames:
            global_data = frame.frame.f_globals.get(global_name, None)
            if global_data is not None:
                return global_data
        else:
            return None

    @staticmethod
    def discover_interface(force_decompiler: str = None, **ctrl_kwargs) -> Optional["DecompilerInterface"]:
        """
        This function is a special API helper that will attempt to detect the decompiler it is running in and
        return the valid BSController for that decompiler. You may also force the chosen controller.

        @param force_decompiler:    The optional string used to override the BSController returned
        @return:                    The DecompilerInterface associated with the current decompiler env
        """
        if force_decompiler and force_decompiler not in YODALIB_SUPPORTED_DECOMPILERS:
            raise ValueError(f"Unsupported decompiler {force_decompiler}")

        has_ida = False
        has_binja = False
        has_angr = False
        is_ghidra = False

        try:
            import idaapi
            has_ida = True
        except ImportError:
            pass

        try:
            import binaryninja
            has_binja = True
        except ImportError:
            pass

        try:
            import angr
            import angrmanagement
            has_angr = DecompilerInterface._find_global_in_call_frames('workspace') is not None
        except ImportError:
            pass

        # we assume if we are nothing else, then we are Ghidra
        is_ghidra = not(has_ida or has_binja or has_angr)

        if has_ida or force_decompiler == IDA_DECOMPILER:
            from yodalib.decompilers.ida.interface import IDAInterface
            dec_controller = IDAInterface(**ctrl_kwargs)
        elif has_binja or force_decompiler == BINJA_DECOMPILER:
            from yodalib.decompilers.binja.controller import BinjaInterface
            bv = DecompilerInterface._find_global_in_call_frames('bv')
            dec_controller = BinjaInterface(bv=bv, **ctrl_kwargs)
        elif has_angr or force_decompiler == ANGR_DECOMPILER:
            from yodalib.decompilers.angr.controller import AngrInterface
            workspace = DecompilerInterface._find_global_in_call_frames('workspace')
            dec_controller = AngrInterface(workspace=workspace, **ctrl_kwargs)
        elif is_ghidra or force_decompiler == GHIDRA_DECOMPILER:
            from yodalib.decompilers.ghidra.server.controller import GhidraInterface
            dec_controller = GhidraInterface(**ctrl_kwargs)
        else:
            raise ValueError("Please use YODALib with our supported decompiler set!")

        return dec_controller
