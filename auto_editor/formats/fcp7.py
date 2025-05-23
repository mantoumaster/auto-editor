from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from math import ceil
from typing import TYPE_CHECKING
from xml.etree.ElementTree import Element

from auto_editor.ffwrapper import FileInfo
from auto_editor.timeline import ASpace, Template, TlAudio, TlVideo, VSpace, v3

from .utils import Validator, show

if TYPE_CHECKING:
    from auto_editor.utils.log import Log

"""
Premiere Pro uses the Final Cut Pro 7 XML Interchange Format

See docs here:
https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/Elements/Elements.html

Also, Premiere itself will happily output subtlety incorrect XML files that don't
come back the way they started.
"""

DEPTH = "16"


def uri_to_path(uri: str) -> str:
    urllib = __import__("urllib.parse", fromlist=["parse"])

    if uri.startswith("file://localhost/"):
        uri = uri[16:]
    elif uri.startswith("file://"):
        # Windows-style paths
        if len(uri) > 8 and uri[9] == ":":
            uri = uri[8:]
        else:
            uri = uri[7:]
    else:
        return uri

    return urllib.parse.unquote(uri)

    # /Users/wyattblue/projects/auto-editor/example.mp4
    # file:///Users/wyattblue/projects/auto-editor/example.mp4
    # file:///C:/Users/WyattBlue/projects/auto-editor/example.mp4
    # file://localhost/Users/wyattblue/projects/auto-editor/example.mp4


def set_tb_ntsc(tb: Fraction) -> tuple[int, str]:
    # See chart: https://developer.apple.com/library/archive/documentation/AppleApplications/Reference/FinalCutPro_XML/FrameRate/FrameRate.html#//apple_ref/doc/uid/TP30001158-TPXREF103
    if tb == Fraction(24000, 1001):
        return 24, "TRUE"
    if tb == Fraction(30000, 1001):
        return 30, "TRUE"
    if tb == Fraction(60000, 1001):
        return 60, "TRUE"

    ctb = ceil(tb)
    if ctb not in {24, 30, 60} and ctb * Fraction(999, 1000) == tb:
        return ctb, "TRUE"

    return int(tb), "FALSE"


def read_tb_ntsc(tb: int, ntsc: bool) -> Fraction:
    if ntsc:
        if tb == 24:
            return Fraction(24000, 1001)
        if tb == 30:
            return Fraction(30000, 1001)
        if tb == 60:
            return Fraction(60000, 1001)
        return tb * Fraction(999, 1000)

    return Fraction(tb)


def speedup(speed: float) -> Element:
    fil = Element("filter")
    effect = ET.SubElement(fil, "effect")
    ET.SubElement(effect, "name").text = "Time Remap"
    ET.SubElement(effect, "effectid").text = "timeremap"

    para = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(para, "parameterid").text = "variablespeed"
    ET.SubElement(para, "name").text = "variablespeed"
    ET.SubElement(para, "valuemin").text = "0"
    ET.SubElement(para, "valuemax").text = "1"
    ET.SubElement(para, "value").text = "0"

    para2 = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(para2, "parameterid").text = "speed"
    ET.SubElement(para2, "name").text = "speed"
    ET.SubElement(para2, "valuemin").text = "-100000"
    ET.SubElement(para2, "valuemax").text = "100000"
    ET.SubElement(para2, "value").text = str(speed)

    para3 = ET.SubElement(effect, "parameter", authoringApp="PremierePro")
    ET.SubElement(para3, "parameterid").text = "frameblending"
    ET.SubElement(para3, "name").text = "frameblending"
    ET.SubElement(para3, "value").text = "FALSE"

    return fil


SUPPORTED_EFFECTS = ("timeremap",)


def read_filters(clipitem: Element, log: Log) -> float:
    for effect_tag in clipitem:
        if effect_tag.tag in {"enabled", "start", "end"}:
            continue
        if len(effect_tag) < 3:
            log.error("<effect> requires: <effectid> <name> and one <parameter>")
        for i, effects in enumerate(effect_tag):
            if i == 0 and effects.tag != "name":
                log.error("<effect>: <name> must be first tag")
            if i == 1 and effects.tag != "effectid":
                log.error("<effect>: <effectid> must be second tag")
                if effects.text not in SUPPORTED_EFFECTS:
                    log.error(f"`{effects.text}` is not a supported effect.")

            if i > 1:
                for j, parms in enumerate(effects):
                    if j == 0:
                        if parms.tag != "parameterid":
                            log.error("<parameter>: <parameterid> must be first tag")
                        if parms.text != "speed":
                            break

                    if j > 0 and parms.tag == "value":
                        if parms.text is None:
                            log.error("<value>: number required")
                        return float(parms.text) / 100

    return 1.0


def fcp7_read_xml(path: str, log: Log) -> v3:
    def xml_bool(val: str) -> bool:
        if val == "TRUE":
            return True
        if val == "FALSE":
            return False
        raise TypeError("Value must be 'TRUE' or 'FALSE'")

    try:
        tree = ET.parse(path)
    except FileNotFoundError:
        log.error(f"Could not find '{path}'")

    root = tree.getroot()

    valid = Validator(log)

    valid.check(root, "xmeml")
    valid.check(root[0], "sequence")
    result = valid.parse(
        root[0],
        {
            "name": str,
            "duration": int,
            "rate": {
                "timebase": Fraction,
                "ntsc": xml_bool,
            },
            "media": None,
        },
    )

    tb = read_tb_ntsc(result["rate"]["timebase"], result["rate"]["ntsc"])

    av = valid.parse(
        result["media"],
        {
            "video": None,
            "audio": None,
        },
    )

    sources: dict[str, FileInfo] = {}
    vobjs: VSpace = []
    aobjs: ASpace = []

    vclip_schema = {
        "format": {
            "samplecharacteristics": {
                "width": int,
                "height": int,
            },
        },
        "track": {
            "__arr": "",
            "clipitem": {
                "__arr": "",
                "start": int,
                "end": int,
                "in": int,
                "out": int,
                "file": None,
                "filter": None,
            },
        },
    }

    aclip_schema = {
        "format": {"samplecharacteristics": {"samplerate": int}},
        "track": {
            "__arr": "",
            "clipitem": {
                "__arr": "",
                "start": int,
                "end": int,
                "in": int,
                "out": int,
                "file": None,
                "filter": None,
            },
        },
    }

    sr = 48000
    res = (1920, 1080)

    if "video" in av:
        tracks = valid.parse(av["video"], vclip_schema)

        if "format" in tracks:
            width = tracks["format"]["samplecharacteristics"]["width"]
            height = tracks["format"]["samplecharacteristics"]["height"]
            res = width, height

        for t, track in enumerate(tracks["track"]):
            if len(track["clipitem"]) > 0:
                vobjs.append([])
            for i, clipitem in enumerate(track["clipitem"]):
                file_id = clipitem["file"].attrib["id"]
                if file_id not in sources:
                    fileobj = valid.parse(clipitem["file"], {"pathurl": str})

                    if "pathurl" in fileobj:
                        sources[file_id] = FileInfo.init(
                            uri_to_path(fileobj["pathurl"]),
                            log,
                        )
                    else:
                        show(clipitem["file"], 3)
                        log.error(
                            f"'pathurl' child element not found in {clipitem['file'].tag}"
                        )

                if "filter" in clipitem:
                    speed = read_filters(clipitem["filter"], log)
                else:
                    speed = 1.0

                start = clipitem["start"]
                dur = clipitem["end"] - start
                offset = clipitem["in"]

                vobjs[t].append(
                    TlVideo(start, dur, sources[file_id], offset, speed, stream=0)
                )

    if "audio" in av:
        tracks = valid.parse(av["audio"], aclip_schema)
        if "format" in tracks:
            sr = tracks["format"]["samplecharacteristics"]["samplerate"]

        for t, track in enumerate(tracks["track"]):
            if len(track["clipitem"]) > 0:
                aobjs.append([])
            for i, clipitem in enumerate(track["clipitem"]):
                file_id = clipitem["file"].attrib["id"]
                if file_id not in sources:
                    fileobj = valid.parse(clipitem["file"], {"pathurl": str})
                    sources[file_id] = FileInfo.init(
                        uri_to_path(fileobj["pathurl"]), log
                    )

                if "filter" in clipitem:
                    speed = read_filters(clipitem["filter"], log)
                else:
                    speed = 1.0

                start = clipitem["start"]
                dur = clipitem["end"] - start
                offset = clipitem["in"]

                aobjs[t].append(
                    TlAudio(
                        start, dur, sources[file_id], offset, speed, volume=1, stream=0
                    )
                )

    T = Template.init(sources[next(iter(sources))], sr, res=res)
    return v3(tb, "#000", T, vobjs, aobjs, v1=None)


def media_def(
    filedef: Element, url: str, src: FileInfo, tl: v3, tb: int, ntsc: str
) -> None:
    ET.SubElement(filedef, "name").text = src.path.stem
    ET.SubElement(filedef, "pathurl").text = url

    timecode = ET.SubElement(filedef, "timecode")
    ET.SubElement(timecode, "string").text = "00:00:00:00"
    ET.SubElement(timecode, "displayformat").text = "NDF"
    rate = ET.SubElement(timecode, "rate")
    ET.SubElement(rate, "timebase").text = f"{tb}"
    ET.SubElement(rate, "ntsc").text = ntsc

    rate = ET.SubElement(filedef, "rate")
    ET.SubElement(rate, "timebase").text = f"{tb}"
    ET.SubElement(rate, "ntsc").text = ntsc

    # DaVinci Resolve needs this tag even though it's blank
    ET.SubElement(filedef, "duration").text = ""

    mediadef = ET.SubElement(filedef, "media")

    if len(src.videos) > 0:
        videodef = ET.SubElement(mediadef, "video")

        vschar = ET.SubElement(videodef, "samplecharacteristics")
        rate = ET.SubElement(vschar, "rate")
        ET.SubElement(rate, "timebase").text = f"{tb}"
        ET.SubElement(rate, "ntsc").text = ntsc
        ET.SubElement(vschar, "width").text = f"{tl.res[0]}"
        ET.SubElement(vschar, "height").text = f"{tl.res[1]}"
        ET.SubElement(vschar, "pixelaspectratio").text = "square"

    for aud in src.audios:
        audiodef = ET.SubElement(mediadef, "audio")
        aschar = ET.SubElement(audiodef, "samplecharacteristics")
        ET.SubElement(aschar, "depth").text = DEPTH
        ET.SubElement(aschar, "samplerate").text = f"{tl.sr}"
        ET.SubElement(audiodef, "channelcount").text = f"{aud.channels}"


def resolve_write_audio(audio: Element, make_filedef, tl: v3) -> None:
    for t, aclips in enumerate(tl.a):
        track = ET.SubElement(audio, "track")
        for j, aclip in enumerate(aclips):
            src = aclip.src

            _start = f"{aclip.start}"
            _end = f"{aclip.start + aclip.dur}"
            _in = f"{aclip.offset}"
            _out = f"{aclip.offset + aclip.dur}"

            if not src.videos:
                clip_item_num = j + 1
            else:
                clip_item_num = len(aclips) + 1 + j

            clipitem = ET.SubElement(track, "clipitem", id=f"clipitem-{clip_item_num}")
            ET.SubElement(clipitem, "name").text = src.path.stem
            ET.SubElement(clipitem, "start").text = _start
            ET.SubElement(clipitem, "end").text = _end
            ET.SubElement(clipitem, "enabled").text = "TRUE"
            ET.SubElement(clipitem, "in").text = _in
            ET.SubElement(clipitem, "out").text = _out

            make_filedef(clipitem, aclip.src)

            sourcetrack = ET.SubElement(clipitem, "sourcetrack")
            ET.SubElement(sourcetrack, "mediatype").text = "audio"
            ET.SubElement(sourcetrack, "trackindex").text = f"{t + 1}"

            if src.videos:
                link = ET.SubElement(clipitem, "link")
                ET.SubElement(link, "linkclipref").text = f"clipitem-{j + 1}"
                ET.SubElement(link, "mediatype").text = "video"
                link = ET.SubElement(clipitem, "link")
                ET.SubElement(link, "linkclipref").text = f"clipitem-{clip_item_num}"

            if aclip.speed != 1:
                clipitem.append(speedup(aclip.speed * 100))


def premiere_write_audio(audio: Element, make_filedef, tl: v3) -> None:
    ET.SubElement(audio, "numOutputChannels").text = "2"
    aformat = ET.SubElement(audio, "format")
    aschar = ET.SubElement(aformat, "samplecharacteristics")
    ET.SubElement(aschar, "depth").text = DEPTH
    ET.SubElement(aschar, "samplerate").text = f"{tl.sr}"

    has_video = tl.v and tl.v[0]
    t = 0
    for aclips in tl.a:
        for channelcount in range(0, 2):  # Because "stereo" is hardcoded.
            t += 1
            track = ET.Element(
                "track",
                currentExplodedTrackIndex=f"{channelcount}",
                totalExplodedTrackCount="2",  # Because "stereo" is hardcoded.
                premiereTrackType="Stereo",
            )

            if has_video:
                ET.SubElement(track, "outputchannelindex").text = f"{channelcount + 1}"

            for j, aclip in enumerate(aclips):
                src = aclip.src

                _start = f"{aclip.start}"
                _end = f"{aclip.start + aclip.dur}"
                _in = f"{aclip.offset}"
                _out = f"{aclip.offset + aclip.dur}"

                if not has_video:
                    clip_item_num = j + 1
                else:
                    clip_item_num = len(aclips) + 1 + j + (t * len(aclips))

                clipitem = ET.SubElement(
                    track,
                    "clipitem",
                    id=f"clipitem-{clip_item_num}",
                    premiereChannelType="stereo",
                )
                ET.SubElement(clipitem, "name").text = src.path.stem
                ET.SubElement(clipitem, "enabled").text = "TRUE"
                ET.SubElement(clipitem, "start").text = _start
                ET.SubElement(clipitem, "end").text = _end
                ET.SubElement(clipitem, "in").text = _in
                ET.SubElement(clipitem, "out").text = _out

                make_filedef(clipitem, aclip.src)

                sourcetrack = ET.SubElement(clipitem, "sourcetrack")
                ET.SubElement(sourcetrack, "mediatype").text = "audio"
                ET.SubElement(sourcetrack, "trackindex").text = f"{t}"
                labels = ET.SubElement(clipitem, "labels")
                ET.SubElement(labels, "label2").text = "Iris"

                if aclip.speed != 1:
                    clipitem.append(speedup(aclip.speed * 100))

            audio.append(track)


def fcp7_write_xml(name: str, output: str, resolve: bool, tl: v3) -> None:
    width, height = tl.res
    timebase, ntsc = set_tb_ntsc(tl.tb)

    src_to_url: dict[FileInfo, str] = {}
    src_to_id: dict[FileInfo, str] = {}

    file_defs: set[str] = set()  # Contains urls

    for src in set(tl.sources):
        the_id = f"file-{len(src_to_id) + 1}"
        src_to_url[src] = f"{src.path.resolve()}"
        src_to_id[src] = the_id

    def make_filedef(clipitem: Element, src: FileInfo) -> None:
        pathurl = src_to_url[src]
        filedef = ET.SubElement(clipitem, "file", id=src_to_id[src])
        if pathurl not in file_defs:
            media_def(filedef, pathurl, src, tl, timebase, ntsc)
            file_defs.add(pathurl)

    xmeml = ET.Element("xmeml", version="5")
    if resolve:
        sequence = ET.SubElement(xmeml, "sequence")
    else:
        sequence = ET.SubElement(xmeml, "sequence", explodedTracks="true")

    ET.SubElement(sequence, "name").text = name
    ET.SubElement(sequence, "duration").text = f"{int(tl.out_len())}"
    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = f"{timebase}"
    ET.SubElement(rate, "ntsc").text = ntsc
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    vformat = ET.SubElement(video, "format")
    vschar = ET.SubElement(vformat, "samplecharacteristics")

    ET.SubElement(vschar, "width").text = f"{width}"
    ET.SubElement(vschar, "height").text = f"{height}"
    ET.SubElement(vschar, "pixelaspectratio").text = "square"

    rate = ET.SubElement(vschar, "rate")
    ET.SubElement(rate, "timebase").text = f"{timebase}"
    ET.SubElement(rate, "ntsc").text = ntsc

    if len(tl.v) > 0 and len(tl.v[0]) > 0:
        track = ET.SubElement(video, "track")

        for j, clip in enumerate(tl.v[0]):
            assert isinstance(clip, TlVideo)

            _start = f"{clip.start}"
            _end = f"{clip.start + clip.dur}"
            _in = f"{clip.offset}"
            _out = f"{clip.offset + clip.dur}"

            this_clipid = f"clipitem-{j + 1}"
            clipitem = ET.SubElement(track, "clipitem", id=this_clipid)
            ET.SubElement(clipitem, "name").text = src.path.stem
            ET.SubElement(clipitem, "enabled").text = "TRUE"
            ET.SubElement(clipitem, "start").text = _start
            ET.SubElement(clipitem, "end").text = _end
            ET.SubElement(clipitem, "in").text = _in
            ET.SubElement(clipitem, "out").text = _out

            make_filedef(clipitem, clip.src)

            ET.SubElement(clipitem, "compositemode").text = "normal"
            if clip.speed != 1:
                clipitem.append(speedup(clip.speed * 100))

            if resolve:
                link = ET.SubElement(clipitem, "link")
                ET.SubElement(link, "linkclipref").text = this_clipid
                link = ET.SubElement(clipitem, "link")
                ET.SubElement(
                    link, "linkclipref"
                ).text = f"clipitem-{(len(tl.v[0])) + j + 1}"
                continue

            for i in range(1 + len(src.audios) * 2):  # `2` because stereo.
                link = ET.SubElement(clipitem, "link")
                ET.SubElement(
                    link, "linkclipref"
                ).text = f"clipitem-{(i * (len(tl.v[0]))) + j + 1}"
                ET.SubElement(link, "mediatype").text = "video" if i == 0 else "audio"
                ET.SubElement(link, "trackindex").text = f"{max(i, 1)}"
                ET.SubElement(link, "clipindex").text = f"{j + 1}"

    # Audio definitions and clips
    audio = ET.SubElement(media, "audio")
    if resolve:
        resolve_write_audio(audio, make_filedef, tl)
    else:
        premiere_write_audio(audio, make_filedef, tl)

    tree = ET.ElementTree(xmeml)
    ET.indent(tree, space="  ", level=0)
    if output == "-":
        print(ET.tostring(xmeml, encoding="unicode"))
    else:
        tree.write(output, xml_declaration=True, encoding="utf-8")
