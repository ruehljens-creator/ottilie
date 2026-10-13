"""Attrappen für Pro Tools (PTSL) – kein Test spricht mit dem echten Pro Tools."""
import grpc
from ptsl.errors import CommandError
from ptsl.PTSL_pb2 import CommandError as CommandErrorResponse
from ptsl.PTSL_pb2 import CommandErrorType


class FakeRpcError(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


def command_error(name, message="Fehler"):
    return CommandError([CommandErrorResponse(
        command_error_type=CommandErrorType.Value(name), command_error_message=message)])


class _RawClient:
    def __init__(self):
        self.timeouts = []

    def SendGrpcRequest(self, request, timeout=None):
        self.timeouts.append(timeout)
        return "antwort"


class _Client:
    def __init__(self, engine=None):
        self.raw_client = _RawClient()
        self._engine = engine

    def run_command(self, command_id, request):
        return self._engine.run_command(command_id, request)

    def run(self, op):
        """Nur ExportSessionInfoAsText wird über ops ausgeführt."""
        import types
        self._engine._maybe_fail("ExportSessionInfoAsText")
        op.response = types.SimpleNamespace(session_info=self._engine.session_text())


class _Track:
    def __init__(self, name, kind, clips, hidden=False, folder="", is_open=True, automation=False):
        import ptsl.PTSL_pb2 as pt
        self.name = name
        self.type = pt.TrackType.Value(kind)
        self.parent_folder_name = folder
        self.track_attributes = type("A", (), {"contains_clips": clips, "is_hidden": hidden, "is_open": is_open,
                                               "contains_automation": automation})()


class _Builder:
    def __init__(self, engine, group, name, session_name, path):
        self.engine, self.args, self.opts = engine, (group, name, session_name, path), {}

    def sample_rate(self, v):
        self.opts["sample_rate"] = v

    def bit_depth(self, v):
        self.opts["bit_depth"] = v

    def wave_format(self):
        self.opts["wave"] = True

    def interleaved(self, v):
        self.opts["interleaved"] = v

    def create(self):
        e = self.engine
        e._maybe_fail("create_session")
        group, name, session_name, path = self.args
        e.created.append((self.args, dict(self.opts)))
        if getattr(e, "created_but_not_opened", False):        # Pro Tools nach Neustart (live)
            return
        e.session = (session_name, f"{path}/{session_name}/{session_name}.ptx")
        e.track_objs = [_Track(t, "AudioTrack", t in e.template_clips) for t in e.template_tracks]
        if e.video_track:
            e.track_objs.append(_Track(e.video_track, "VideoTrack", bool(e.video_clips)))
            if e.video_clips:
                e.extents[e.video_track] = e.video_clips
        e.clips = {t: [(a, b, "vorhanden") for a, b in c] for t, c in e.template_clips.items()}


class FakeEngine:
    """Merkt sich Aufrufe; `fail` legt je Methode eine Ausnahme fest."""

    def __init__(self, session=("Beitrag", "/Sessions/Beitrag/Beitrag.ptx"),
                 transport=("TS_TransportStopped",), version=2025, fail=None,
                 template_tracks=("ST", "IT", "OGA", "Mus", "Spr", "A1", "A2"),
                 aaf_tracks=("Audio 1", "Audio 2"), video_failure=None,
                 aaf_clips=None, template_clips=None, video_track=None, video_new_track=False,
                 video_clips=None, video_single_clip=False):
        self.client = _Client(self)
        self.template_tracks = list(template_tracks)
        self.aaf_tracks = list(aaf_tracks)
        self.video_failure = video_failure
        self.aaf_clips = dict(aaf_clips or {})          # Spur → [(Anfang, Ende)] in Frames
        self.template_clips = dict(template_clips or {})
        self.video_track = video_track                  # vorhandene Videospur im Template
        self.video_new_track = video_new_track          # Fehlverhalten nachstellen
        self.video_clips = video_clips                  # (Anfang, Ende) im Template, z. B. Startband
        self.video_single_clip = video_single_clip      # Pro Tools Studio: nur ein Videoclip
        self.extents = {}                               # Spur → (Anfang, Ende) für SelectAllClipsOnTrack
        self.edit_mode = "EMO_Shuffle"
        self.selected = []
        self.clips = {}
        self.track_objs = []
        self.created = []
        self.requests = []
        self.session = session
        self.transport = list(transport)
        self.version = version
        self.fail = dict(fail or {})
        self.calls = []
        self.closed = False
        self.start_time = "00:00:00:00"                 # Sessionbeginn (GetSessionStartTime)

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]

    def ptsl_version(self):
        self._maybe_fail("ptsl_version")
        return self.version

    def session_name(self):
        self._maybe_fail("session_name")
        if self.session is None:
            raise command_error("PT_NoOpenedSession")
        return self.session[0]

    def session_path(self):
        self._maybe_fail("session_path")
        if self.session is None:
            raise command_error("PT_NoOpenedSession")
        return self.session[1]

    def session_start_time(self):
        if "session_start_time" in self.fail:          # nicht in `calls`: bestehende Ablaufprüfungen bleiben gleich
            raise self.fail["session_start_time"]
        return self.start_time

    def transport_state(self):
        self._maybe_fail("transport_state")
        return self.transport.pop(0) if len(self.transport) > 1 else self.transport[0]

    def save_session(self):
        self._maybe_fail("save_session")

    def close_session(self, save_on_close):
        self._maybe_fail(f"close_session(save_on_close={save_on_close})")
        self.session = None

    def save_session_as(self, path, name):
        self._maybe_fail(f"save_session_as({path}, {name})")
        import os
        self.session = (name, os.path.join(path, f"{name}.ptx"))

    def open_session(self, path):
        self._maybe_fail(f"open_session({path})")
        import os
        self.session = (os.path.splitext(os.path.basename(path))[0], path)

    def close(self):
        self.closed = True

    # ── Export: Auswahl, Kürzen, Konsolidieren ─────────────────────────────
    video_end = "10:00:30:00.00"
    consolidate_writes = True

    video_start = "10:00:00:00.00"

    def select_all_clips_on_track(self, name):
        self.calls.append(f"select_all_clips_on_track({name})")
        self.selected = [name]
        if self.clips.get(name) and name not in self.extents:
            a = min(x[0] for x in self.clips[name])
            b = max(x[1] for x in self.clips[name])
            self._extent = (_tc(a), _tc(b))
            return
        self._extent = self.extents.get(name, (self.video_start, self.video_end))

    def get_timeline_selection(self):
        return getattr(self, "_extent", ("10:00:00:00.00", self.video_end))

    def select_tracks_by_name(self, names):
        # wie Pro Tools: auf ausgeblendeten Spuren entsteht keine Auswahl (live 02.10.2026)
        self.selected = [n for n in names if n not in (getattr(self, "hidden", None) or set())]

    def set_timeline_selection(self, in_time, out_time):
        self.calls.append(f"selection {in_time}–{out_time}")
        self._extent = (in_time, out_time)

    def extend_selection_to_target_tracks(self, names):
        pass

    def clear(self):
        self.calls.append(f"clear {','.join(self.selected)}")

    def consolidate_clip(self):
        import numpy as np, soundfile as sf, os
        self.calls.append(f"consolidate {','.join(self.selected)}")
        if not self.consolidate_writes:
            return
        audio = os.path.join(os.path.dirname(self.session[1]), "Audio Files")
        os.makedirs(audio, exist_ok=True)
        rng = np.random.default_rng(1)
        for t in self.selected:
            sf.write(os.path.join(audio, f"{t}_01.wav"), rng.standard_normal((48000 * 3, 2)) * 0.02,
                     48000, subtype="PCM_24")

    def refresh_all_modified_audio_files(self):
        pass

    def set_track_hidden_state(self, names, hidden):
        self.calls.append(f"hidden={hidden} {','.join(names)}")
        if getattr(self, "hidden", None) is not None and not hidden:
            self.hidden -= set(names)

    def toggle_play_state(self):
        self.calls.append("toggle_play_state")

    def rename_target_clip(self, *a, **k):
        raise RuntimeError("Attrappe")

    def create_session_from_template(self, group, name, session_name, path):
        return _Builder(self, group, name, session_name, path)

    def track_list(self):
        self._maybe_fail("track_list")
        return list(self.track_objs)

    def run_command(self, command_id, request):
        import ptsl.PTSL_pb2 as pt
        name = pt.CommandId.Name(command_id)
        self._maybe_fail(name)
        self.requests.append((name, request))
        if name == "CreateNewTracks":
            self.track_objs.append(_Track(request["track_name"], "AudioTrack", False))
            return {}
        if name == "RenameTargetTrack":
            for t in self.track_objs:
                if t.name == request["current_name"]:
                    t.name = request["new_name"]
            if request["current_name"] in self.clips:
                self.clips[request["new_name"]] = self.clips.pop(request["current_name"])
            return {}
        if name == "GetEditMode":
            return {"current_setting": self.edit_mode}
        if name in ("SetTrackHiddenState", "SetTrackOpenState"):
            attr = "is_hidden" if name == "SetTrackHiddenState" else "is_open"
            for t in self.track_objs:
                if t.name in request["track_names"]:
                    setattr(t.track_attributes, attr, request["enabled"])
            return {}
        if name == "SetEditMode":
            self.edit_mode = request["edit_mode"]
            return {}
        if name == "Clear":
            from tonmischung.core.timecode import tc_to_frames
            a, b = (tc_to_frames(x) for x in self._extent)
            for t in self.selected:
                self.clips[t] = [c for c in self.clips.get(t, []) if not (c[0] >= a and c[1] <= b)]
            return {}
        if name in ("Cut", "Copy"):
            from tonmischung.core.timecode import tc_to_frames
            a, b = (tc_to_frames(x) for x in self._extent)
            self.clipboard = []
            for t in self.selected:
                inside = [c for c in self.clips.get(t, []) if c[0] >= a and c[1] <= b]
                self.clipboard.append(inside)
                if name == "Cut":
                    self.clips[t] = [c for c in self.clips.get(t, []) if c not in inside]
            return {}
        if name == "Paste":
            if not getattr(self, "clipboard", None):
                raise command_error("PT_Forbidden", "Paste nicht möglich")
            for t, part in zip(self.selected, self.clipboard):
                self.clips.setdefault(t, []).extend(part)
                for tr in self.track_objs:
                    if tr.name == t:
                        tr.track_attributes.contains_clips = True
            return {}
        if name == "GetEditModeOptions":
            return {"edit_mode_options": {"link_timeline_and_edit_selection": True}}
        if name == "GetEditTool":
            return {"current_setting": "ET_SmartTool"}
        if name == "Import":
            sel = getattr(self, "_extent", None)
            if sel and sel[0] != sel[1]:
                return {}                     # Pro Tools: Auswahl begrenzt den Import still (live)
            sd = request["session_data"]
            match = sd["match_options"] == "MT_MatchTracks"
            delta = 0
            if sd.get("timecode_mapping_units") == "MapStartTimeCodeTo":
                h, m, s_, f = (int(x) for x in sd["timecode_mapping_start_time"].split(":"))
                delta = ((h * 60 + m) * 60 + s_) * 25 + f - getattr(self, "aaf_start", 900000)
            replace = sd.get("playlist_options") == "ImportReplaceExistingPlaylists"
            vorhanden = {t.name: t for t in self.track_objs}
            for t in self.aaf_tracks:
                if match and t not in vorhanden:
                    continue                                   # Pro Tools verwirft still (§29)
                ziel = t
                if not match:
                    if t in vorhanden:
                        ziel = f"{t}.1"                        # Pro Tools hängt bei Namensgleichheit an
                    self.track_objs.append(_Track(ziel, "AudioTrack", True))
                else:
                    vorhanden[t].track_attributes.contains_clips = True
                    if replace:
                        self.clips[t] = []                     # Hauptplaylist ersetzt
                self.clips.setdefault(ziel, []).extend((a + delta, b + delta, "neu") for a, b in self.aaf_clips.get(t, []))
        elif name == "ImportVideo":
            import os
            import ptsl.PTSL_pb2 as pt
            if self.video_failure:
                return {"failure_list": [self.video_failure]}
            if self.video_track and not self.video_new_track:
                v = [t for t in self.track_objs if "Video" in pt.TrackType.Name(t.type)][0]
                v.track_attributes.contains_clips = True
                start = request["spot_location_data"]["location"]["location"] + ".00"
                if self.video_single_clip:                   # ersetzt den Clip, Spur heißt wie er
                    self.extents.pop(v.name, None)
                    v.name = os.path.splitext(os.path.basename(request["video_file_list"][0]))[0]
                    self.extents[v.name] = (start, self.video_end)
                else:
                    self.extents[v.name] = (min(self.extents.get(v.name, (start,))[0], start), self.video_end)
            else:                                            # neue Spur, nach dem Clip benannt (live 04.10.2026)
                self.track_objs.append(_Track(os.path.splitext(os.path.basename(request["video_file_list"][0]))[0],
                                              "VideoTrack", True))
        return {}

    def session_text(self):
        def tc(f):
            return f"{f // 90000:02d}:{f // 1500 % 60:02d}:{f // 25 % 60:02d}:{f % 25:02d}"
        teile = ["SESSION NAME:\tTest\n"]
        for t in self.track_objs:
            teile.append(f"\nTRACK NAME:\t{t.name}\nCHANNEL \tEVENT   \tCLIP NAME\n")
            for i, (a, b, n) in enumerate(sorted(self.clips.get(t.name, [])), 1):
                teile.append(f"1       \t{i}       \t{n}\t{tc(a)}\t{tc(b)}\t{tc(b - a)}\tUnmuted\n")
        return "".join(teile)


def _tc(frames):
    from tonmischung.core.timecode import frames_to_tc
    return frames_to_tc(frames) + ".00"
