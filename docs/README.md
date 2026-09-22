Source files for everything in `docs/`. To rebuild after editing (macOS, Google Chrome):

```bash
C="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# diagram (README image)
"$C" --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1400,1491 \
  --virtual-time-budget=8000 --screenshot="$PWD/how-it-works.png" \
  "file://$PWD/docs/how-it-works.html"

# lead scraper guide (3 pages)
"$C" --headless=new --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf="$PWD/Lead-Scraper-Guide.pdf" "file://$PWD/docs/guide.html"

# mailbox + campaign cheat sheet (1 page)
"$C" --headless=new --no-pdf-header-footer --virtual-time-budget=8000 \
  --print-to-pdf="$PWD/Mailbox-and-Campaign-Cheat-Sheet.pdf" \
  "file://$PWD/docs/sop-mailboxes-campaigns.html"
```
