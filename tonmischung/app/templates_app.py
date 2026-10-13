"""Templates im Fenster: die Auswahl beim Holen (`library`) und „Meine Templates“ (Plan Nutzer & Räume, Schritt 3;
Jens 04.10.2026): hochladen, aus der Pro-Tools-Bibliothek übernehmen, Standard festlegen, löschen (hochgeladene ins
Archiv, übernommene nur aus der Liste). Die Liste und der Standard sind Nutzer-Einstellungen (`persoenlich.templates`,
`persoenlich.standard_template_id`)."""
from __future__ import annotations

import logging
import os
from typing import Any

from tonmischung.app import choosers
from tonmischung.app.bridge import Bridge, CommandError
from tonmischung.app.context import AppContext
from tonmischung.core import user_templates as UT
from tonmischung.core.users import ARCHIVE, slug

P_LIST, P_STD = "persoenlich.templates", "persoenlich.standard_template_id"
log = logging.getLogger("tonmischung.app")


def library(ctx: AppContext, show: str = "") -> dict:
    """„Meine Templates“ aus den Einstellungen und die Templates, die Pro Tools auf diesem Mac hat.

    PTSL liefert keine Template-Liste; Pro Tools legt sie als `<Gruppe>/<Name>.ptxt` bzw. als
    Ordner `<Gruppe>/<Name>/<Name>.ptxt` ab. Vorgewählt ist der Standard oder – je nach
    Einstellung – das zuletzt für die Sendung `show` benutzte Template.
    """
    from tonmischung.core import templates as tpl
    s = ctx.settings
    own = tpl.from_settings(s.get(P_LIST) or [])
    mine = [{"group": t.group, "name": t.name, "label": t.label, "id": t.id, "show": t.show,
             "info": next((str(e.get("info", "")) for e in (s.get(P_LIST) or []) if e.get("id") == t.id), "")}
            for t in own]
    found: list[tpl.Template] = []
    root = os.path.expanduser(_pt_root(ctx))
    if os.path.isdir(root):
        for group in sorted(os.listdir(root)):
            gdir = os.path.join(root, group)
            if not os.path.isdir(gdir) or group.startswith("."):
                continue
            for entry in sorted(os.listdir(gdir)):
                name = entry[:-5] if entry.endswith(".ptxt") else entry
                if entry.endswith(".ptxt") or os.path.exists(os.path.join(gdir, entry, f"{entry}.ptxt")):
                    found.append(tpl.Template(group, name, name))
    pick = tpl.preselect(own, found, s.get(P_STD) or "", s.get("persoenlich.zuletzt_je_sendung") or {}, show,
                         bool(s.get("persoenlich.vorschlag_nach_sendung")))
    if pick is None and ctx.mock is not None:      # Entwicklungsmodus: Test-Template (testfiles/entwicklung.json)
        from tonmischung import devlocal
        dev = next((t for key in devlocal.dev_templates() for t in found if f"{t.group}/{t.name}" == key), None)
        pick = tpl.Preselection(dev, "Entwicklung") if dev else None
    known = {(t.group, t.name) for t in own}
    std = next((t for t in own if t.id and t.id == (s.get(P_STD) or "")), None)
    return {"mine": mine, "standard": {"group": std.group, "name": std.name} if std else None,
            "found": [{"group": t.group, "name": t.name, "label": t.label, "info": f"Pro Tools · {t.group}", "id": ""}
                      for t in found if (t.group, t.name) not in known],
            "default": {"group": pick.template.group, "name": pick.template.name} if pick else None,
            "hint": pick.hint if pick else "",
            "triage": bool(s.get("import.zuordnungsregeln.aktiv")),
            "preview": s.get("import.import_vorschau.triage_vorschau") or "nur_bei_unsicherheit"}


def remember(ctx: AppContext, show: str, group: str, name: str) -> None:
    """Nach erfolgreichem Holen: Template als „zuletzt für <Sendung>“ merken."""
    from tonmischung.config.settings import SettingLockedError
    from tonmischung.core import templates as tpl
    path = "persoenlich.zuletzt_je_sendung"
    try:
        ctx.settings.set_local(path, tpl.remember(ctx.settings.get(path) or {}, show, group, name))
    except (SettingLockedError, OSError) as e:             # Merken ist Komfort, kein Fehler des Holens
        log.warning("Template für %s nicht gemerkt: %s", show, e)


def profile_for(ctx: AppContext, group: str, name: str):
    """Spurprofil des Templates (Einstellungen › Import › Spurprofile) oder None – dann gilt alles wie bisher."""
    from tonmischung.core import track_profile as TP
    return TP.find(TP.profiles_from_settings(ctx.settings.get("import.spurprofile")), group, name) if name else None


def _entries(ctx: AppContext) -> list[dict]:
    return [dict(e) for e in (ctx.settings.get(P_LIST) or [])]


def _user_dir(ctx: AppContext) -> tuple[str, str]:
    from tonmischung.app import users_app
    users_app.require_user(ctx)
    st = users_app.store(ctx)
    return st.folder(ctx.user.id), os.path.join(st.root, ARCHIVE)


def view(ctx: AppContext, message: str = "") -> dict[str, Any]:
    std = str(ctx.settings.get(P_STD) or "")
    mine = [{"id": str(e.get("id") or ""), "name": str(e.get("pt_name") or ""), "group": str(e.get("gruppe") or ""),
             "label": str(e.get("anzeigename") or e.get("pt_name") or ""),
             "uploaded": UT.is_user_group(str(e.get("gruppe") or "")),
             "standard": str(e.get("id") or "") == std} for e in _entries(ctx)]
    lib = library(ctx)
    return {"mine": mine, "standard": std, "found": lib["found"], "can_upload": ctx.user is not None,
            "message": message}


def choose_ptxt() -> str:
    return choosers.choose_file("Pro-Tools-Template (.ptxt) wählen")


def _add(ctx: AppContext, entry: dict) -> None:
    entries = _entries(ctx)
    if any(e.get("gruppe") == entry["gruppe"] and e.get("pt_name") == entry["pt_name"] for e in entries):
        raise CommandError(f"„{entry['pt_name']}“ steht schon in „Meine Templates“")
    ids = {e.get("id") for e in entries}
    base, n = entry["id"], 1
    while entry["id"] in ids:
        n += 1
        entry["id"] = f"{base}-{n}"
    entries.append(entry)
    ctx.settings.set_local(P_LIST, entries)
    if not ctx.settings.get(P_STD):
        ctx.settings.set_local(P_STD, entry["id"])           # das erste wird Standard


def upload(ctx: AppContext, args: dict, choose: Any = None) -> dict[str, Any]:
    user_dir, archive_dir = _user_dir(ctx)
    path = str(args.get("path") or "") or (choose or choose_ptxt)()
    if not path:
        return view(ctx, "Hochladen abgebrochen")
    group = UT.group_for(ctx.user.name)
    try:
        name = UT.upload(user_dir, path)
        UT.install(user_dir, name, group, archive_dir, pt_root=_pt_root(ctx))
    except (UT.TemplateError, OSError) as e:
        raise CommandError(str(e)) from e
    _add(ctx, {"id": f"tm-{slug(name)}", "anzeigename": name, "gruppe": group, "pt_name": name, "info": "hochgeladen"})
    return view(ctx, f"„{name}“ hochgeladen und in Pro Tools unter „{group}“ bereitgestellt")


def adopt(ctx: AppContext, args: dict) -> dict[str, Any]:
    group, name = str(args.get("group") or ""), str(args.get("name") or "")
    if not group or not name:
        raise CommandError("Bitte ein Template wählen")
    _add(ctx, {"id": f"pt-{slug(group)}-{slug(name)}", "anzeigename": name, "gruppe": group, "pt_name": name})
    return view(ctx, f"„{name}“ in „Meine Templates“ übernommen")


def set_standard(ctx: AppContext, args: dict) -> dict[str, Any]:
    tid = str(args.get("id") or "")
    if tid and not any(e.get("id") == tid for e in _entries(ctx)):
        raise CommandError("Template nicht in „Meine Templates“")
    ctx.settings.set_local(P_STD, tid)
    return view(ctx, "Standard-Template festgelegt" if tid else "Kein Standard-Template")


def delete(ctx: AppContext, args: dict) -> dict[str, Any]:
    tid = str(args.get("id") or "")
    entries = _entries(ctx)
    e = next((x for x in entries if x.get("id") == tid), None)
    if e is None:
        raise CommandError("Template nicht gefunden")
    name, group = str(e.get("pt_name")), str(e.get("gruppe"))
    note = f"„{name}“ aus „Meine Templates“ genommen (bleibt in Pro Tools)"
    if UT.is_user_group(group):
        user_dir, archive_dir = _user_dir(ctx)
        try:
            dest = UT.archive(user_dir, name, group, archive_dir, ctx.user.id, pt_root=_pt_root(ctx))
        except (UT.TemplateError, OSError) as err:
            raise CommandError(str(err)) from err
        note = f"„{name}“ ins Archiv verschoben: {dest}"
    ctx.settings.set_local(P_LIST, [x for x in entries if x.get("id") != tid])
    if str(ctx.settings.get(P_STD) or "") == tid:
        ctx.settings.set_local(P_STD, "")
    return view(ctx, note)


def ensure_installed(ctx: AppContext, group: str, name: str) -> None:
    """Vor dem Anlegen: hochgeladenes Template in der Pro-Tools-Bibliothek dieses Macs sicherstellen."""
    if not UT.is_user_group(group) or ctx.user is None:
        return
    user_dir, archive_dir = _user_dir(ctx)
    try:
        UT.install(user_dir, name, group, archive_dir, pt_root=_pt_root(ctx))
    except (UT.TemplateError, OSError) as e:
        raise CommandError(f"Template „{name}“: {e}") from e


def _pt_root(ctx: AppContext) -> str:
    return str(ctx.extra.get("pt_template_root") or UT.PT_ROOT)


def register(bridge: Bridge, ctx: AppContext) -> None:
    bridge.register("templates", lambda args: library(ctx, show=str(args.get("show") or "")))
    bridge.register("vorlagen", lambda args: view(ctx))
    bridge.register("vorlage_hochladen", lambda args: upload(ctx, args))
    bridge.register("vorlage_uebernehmen", lambda args: adopt(ctx, args))
    bridge.register("vorlage_standard", lambda args: set_standard(ctx, args))
    bridge.register("vorlage_loeschen", lambda args: delete(ctx, args))
