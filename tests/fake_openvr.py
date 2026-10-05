"""A stand-in for the pyopenvr module, enough to drive frame_voice.vr.SteamVR
frame by frame: which action sets it activates, at what priority, and what
it does with the buttons SteamVR reports."""

import ctypes


class VRActiveActionSet_t(ctypes.Structure):
    _fields_ = [("ulActionSet", ctypes.c_uint64), ("ulRestrictedToDevice", ctypes.c_uint64),
                ("ulSecondaryActionSet", ctypes.c_uint64), ("unPadding", ctypes.c_uint32),
                ("nPriority", ctypes.c_int32)]


class InputError_InvalidPriority(Exception):
    pass


class SettingsError_UnsetSettingHasNoDefault(Exception):
    pass


class Digital:
    def __init__(self, active, state):
        self.bActive, self.bState = active, state


class Event:
    eventType = 0


class FakeOpenVR:
    VRApplication_Background = 3
    VRApplication_Overlay = 2
    k_nActionSetOverlayGlobalPriorityMin = 0x01000000
    k_ulInvalidInputValueHandle = 0
    k_unMaxTrackedDeviceCount = 4
    k_unTrackedDeviceIndex_Hmd = 0
    TrackedDeviceClass_Controller = 2
    TrackedControllerRole_LeftHand = 1
    TrackedControllerRole_RightHand = 2
    Prop_ControllerType_String = 7000
    VREvent_Quit = 700
    VREvent_Input_BindingLoadFailed = 1701
    VREvent_Input_BindingLoadSuccessful = 1702
    VRActiveActionSet_t = VRActiveActionSet_t
    VREvent_t = Event

    def __init__(self):
        self.pressed = set()         # physical buttons held
        self.global_setting = True   # "Enable global input from overlays"
        self.reject_global = False   # SteamVR refuses overlay-global priority
        self.focus = False           # the overlay has input focus
        self.bound = True            # SteamVR loaded our bindings
        self.scene_pid = 0
        self.dashboard = False
        self.legacy_bits = None      # None: legacy API reports nothing
        self.events = []
        self.frames = []             # per frame: {button: priority}
        self.autolaunch = []
        self.inits = []              # VR_Init calls, by application type
        self.shutdowns = 0
        self._sets = {}
        self._actions = {}

    def init(self, kind):
        self.inits.append(kind)

    def shutdown(self):
        self.shutdowns += 1

    # interface accessors
    def VRSystem(self):
        return self

    VRInput = VRSettings = VRApplications = VROverlay = VRSystem

    # IVRSystem
    def pollNextEvent(self, event):
        if not self.events:
            return False
        event.eventType = self.events.pop(0)
        return True

    def acknowledgeQuit_Exiting(self):
        pass

    def getTrackedDeviceClass(self, i):
        return self.TrackedDeviceClass_Controller if i in (1, 2) else 0

    def getControllerRoleForTrackedDeviceIndex(self, i):
        return {1: self.TrackedControllerRole_LeftHand, 2: self.TrackedControllerRole_RightHand}[i]

    def getStringTrackedDeviceProperty(self, i, prop):
        return "frame_controller"

    def getControllerState(self, i):
        if self.legacy_bits is None:
            return False, None
        state = type("State", (), {"ulButtonPressed": self.legacy_bits if i == 2 else 0})()
        return True, state

    # IVRSettings
    def getBool(self, section, key):
        assert (section, key) == ("steamvr", "globalActionSetPriority")
        if self.global_setting is None:
            raise SettingsError_UnsetSettingHasNoDefault()
        return self.global_setting

    def setBool(self, section, key, value):
        assert (section, key) == ("steamvr", "globalActionSetPriority")
        self.global_setting = value
        self.reject_global = False

    # IVRApplications
    def addApplicationManifest(self, path, temporary):
        pass

    def identifyApplication(self, pid, key):
        pass

    def setApplicationAutoLaunch(self, key, on):
        self.autolaunch.append(on)

    def getCurrentSceneProcessId(self):
        return self.scene_pid

    def getApplicationKeyByProcessId(self, pid):
        return "steam.app.123"

    # IVROverlay
    def isDashboardVisible(self):
        return self.dashboard

    def createOverlay(self, key, name):
        raise RuntimeError("no overlays in tests")

    # IVRInput
    def setActionManifestPath(self, path):
        self.manifest = path

    def getActionSetHandle(self, name):
        return self._sets.setdefault(name, len(self._sets) + 1)

    def getActionHandle(self, name):
        return self._actions.setdefault(name, 100 + len(self._actions))

    def updateActionState(self, sets):
        names = {v: k.rsplit("/", 1)[1] for k, v in self._sets.items()}
        frame = {names[s.ulActionSet]: s.nPriority for s in sets}
        if self.reject_global and any(p >= self.k_nActionSetOverlayGlobalPriorityMin
                                      for p in frame.values()):
            self.frames.append({})
            raise InputError_InvalidPriority()
        self.frames.append(frame)

    def getDigitalActionData(self, handle, device):
        name = {v: k for k, v in self._actions.items()}[handle].split("/")[2]
        frame = self.frames[-1] if self.frames else {}
        if name not in frame or not self.bound:
            return Digital(False, False)
        delivered = self.focus or (frame[name] >= self.k_nActionSetOverlayGlobalPriorityMin
                                   and self.global_setting)
        return Digital(True, delivered and name in self.pressed)
