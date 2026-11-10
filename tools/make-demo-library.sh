#!/bin/sh
# Demo library for screenshots: open-licence (Blender) and public-domain titles only, synthetic video
# (test patterns). Creates <dir>/media/{Movies,Shows,Downloads}, about 1.5 GB. Run it in the image:
#   docker run --rm -v "$PWD/tools":/tools:ro -v <dir>:/demo --entrypoint sh mediaman:dev /tools/make-demo-library.sh
# then start MediaMan with <dir>/media mounted at /media and a fresh config folder.
set -e
F=/usr/lib/jellyfin-ffmpeg/ffmpeg
M=/demo/media
mkdir -p "$M/Movies" "$M/Shows" "$M/Downloads"

clip() {   # out, size, seconds, codec args...
  out="$1"; size="$2"; secs="$3"; shift 3
  mkdir -p "$(dirname "$out")"
  $F -v error -y -f lavfi -i "testsrc2=size=$size:rate=24000/1001" \
     -f lavfi -i "sine=frequency=330:sample_rate=48000" -f lavfi -i "sine=frequency=550:sample_rate=48000" \
     -t "$secs" -map 0:v -map 1:a -map 2:a "$@" -c:a aac -b:a 192k -ac:a:0 6 -ac:a:1 2 \
     -metadata:s:a:0 language=eng -metadata:s:a:1 language=fre "$out"
}
h264() { clip "$1" "$2" "$3" -c:v libx264 -preset ultrafast -crf 6 -pix_fmt yuv420p; }
hdr10() { clip "$1" "$2" "$3" -c:v libx265 -preset ultrafast -crf 8 -pix_fmt yuv420p10le \
  -x265-params "log-level=error:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400" \
  -color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc; }

# organized, transcode candidates
h264  "$M/Movies/Big Buck Bunny (2008)/Big Buck Bunny (2008).mkv" 1920x1080 40
h264  "$M/Movies/Sintel (2010)/Sintel (2010).mkv" 1920x1080 40
hdr10 "$M/Movies/Tears of Steel (2012)/Tears of Steel (2012).mkv" 3840x2160 20
h264  "$M/Movies/Night of the Living Dead (1968)/Night of the Living Dead (1968).mkv" 1920x1080 30
h264  "$M/Movies/His Girl Friday (1940)/His Girl Friday (1940).mkv" 1920x1080 30
# need renaming
h264  "$M/Movies/elephants.dream.2006.1080p.bluray.mkv" 1920x1080 25
h264  "$M/Movies/Cosmos Laundromat 2015 1080p.mkv" 1920x1080 25
h264  "$M/Movies/Plan 9 from Outer Space (1959).mkv" 1280x720 25
# a show, one episode badly named
for e in 01 02 03; do h264 "$M/Shows/Pioneer One (2010)/Season 01/Pioneer One S01E$e.mkv" 1280x720 20; done
h264  "$M/Shows/Pioneer One (2010)/pioneer.one.s01e04.720p.mkv" 1280x720 20
# finished downloads
h264  "$M/Downloads/Spring.2019.1080p.WEB.mkv" 1920x1080 25
hdr10 "$M/Downloads/Charge.2022.2160p.HDR10.mkv" 3840x2160 15
h264  "$M/Downloads/Sprite.Fright.2021.1080p.mkv" 1920x1080 25
du -sh "$M"/*
