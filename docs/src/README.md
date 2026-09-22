Source files for the diagram and guide in `docs/`. To rebuild after editing (macOS, Google Chrome):

```bash
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$C" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1400,1491 --virtual-time-budget=8000 --screenshot="$PWD/docs/how-it-works.png" "file://$PWD/docs/src/how-it-works.html"
"$C" --headless=new --no-pdf-header-footer --virtual-time-budget=8000 --print-to-pdf="$PWD/docs/Lead-Scraper-Guide.pdf" "file://$PWD/docs/src/guide.html"
```
