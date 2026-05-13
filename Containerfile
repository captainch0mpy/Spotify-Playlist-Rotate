# Minimal static-file server for the Playlist Rotator app.
# Build:  podman build -t playlist-rotator .
# Run:    podman run -d --name playlist-rotator \
#           -p 127.0.0.1:8080:80 \
#           --restart unless-stopped \
#           playlist-rotator

FROM docker.io/library/nginx:alpine

# Drop the default index and replace with our app.
# Renaming to index.html means the app is reachable at the root URL.
COPY playlist-rotator.html /usr/share/nginx/html/index.html

EXPOSE 80
