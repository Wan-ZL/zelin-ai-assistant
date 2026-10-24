# Roadmap

Where this project goes after lite 2.0. Statuses reflect October 2026. Before picking anything up,
check the in-progress list; some items already have work in flight.

Lite 2.0 is deliberately small: screen recording, the Obsidian ingest chain, meeting recaps and a
Dock app around them. Anything that would grow it back into the 1.x pipeline (radars, approval
cards, background agents) is out of scope; the 1.x tree lives at git tag and branch `full`.

## In progress

- **Visual baselines for the recaps page.** The goldens are captured on the CI runner by the
  `visual-goldens` workflow from fictional demo recaps with a pinned clock
  (`scripts/demo_seed.py --now`); the first lite set is pending one manual dispatch.
- **Developer ID signing and notarization.** The local self-signed identity already keeps the TCC
  grants across upgrades (`shell/build.sh`). What is left is a real Apple Developer ID plus
  notarization, so a downloaded copy no longer needs right-click → Open.

## Next

- **Move the vault mirror out of the git checkout.** The ingest mirror still lives under `state/`
  inside the repository tree; moving it out removes the last way a headless run could see the
  checkout.
- **A prebuilt app that does not depend on the build path.** The zip attached to a release starts
  the server through the LaunchAgent that `install.sh` writes; its own fallback still points at
  the path it was built in.

## Later

- **Stable TCC helper binary.** Split the capture path into a rarely changing helper so app
  updates never touch permissions; the fallback if Developer ID signing does not happen.
