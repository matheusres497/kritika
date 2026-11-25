import protocol


DEFAULT_LICENSES = {"Ep01Stage01": "NORMAL", "Ep01Stage02": "NORMAL", "Ep01Village01": "NORMAL", "RgEvent_JumpingGuide_01": "NORMAL",
                    "RgCdVillage01": "NORMAL", "RgCd01_St01": "NONE", "RgCd01_St02": "NONE", "RgCd01_St03": "NONE",
                    "RgCd01_Dummy": "NONE"}


LEVEL_LICENCES = {"Ep01Stage03": "NORMAL"}


def player_licenses(player):
    lic = player.setdefault("licenses", dict(DEFAULT_LICENSES))
    lic.setdefault("Ep01Stage02", "NORMAL")
    return lic


def grant_by_level(player):
    import sdb_stage
    lic = player_licenses(player)
    new = []
    for stage, difficulty in LEVEL_LICENCES.items():
        need = int(sdb_stage.stage_info(stage).get("MinimumLevelToGetLicense") or 0)
        if stage not in lic and int(player.get("level", 1)) >= need:
            lic[stage] = difficulty
            new.append(stage)
    return new


def grant(player, stage_id, difficulty="NORMAL"):
    lic = player_licenses(player)
    if stage_id in lic:
        return False
    lic[stage_id] = difficulty
    return True


def write_map(w, tag, licenses):
    w.field_tag(tag).begin(protocol.h16("associative_array")).u32(len(licenses))
    for i, (stage, diff) in enumerate(sorted(licenses.items())):
        w.wstr(stage)
        w.field_tag(f"item_second_{i}").begin(protocol.h16("Apple::StageLicense"))
        w.wstr(diff)
        w.i64(i + 1)
        w.u32(0)


def licenses_override(player):
    w = protocol.Writer()
    write_map(w, "Licenses", player_licenses(player))
    return bytes(w.buf)


def set_licenses_packet(player):
    w = protocol.Writer().begin(protocol.registered_id("Apple::Protocol::FromServer::ActorSystem::SetLicenses"))
    write_map(w, "Licenses", player_licenses(player))
    return protocol.packet(bytes(w.buf))


def playerinfo_licenses_override(player):
    w = protocol.Writer()
    lic = player_licenses(player)
    w.field_tag("Licenses").begin(protocol.h16("associative_array")).u32(len(lic))
    for stage, diff in sorted(lic.items()):
        w.wstr(stage).wstr(diff)
    return bytes(w.buf)
