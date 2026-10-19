# Board Game Player Screen Holder Generator

A Python script that generates 3D-printable holders for tri-fold board game player screens. Each holder has slots for the screen, two token trays and a name plaque on the table-facing side. The plaques slide on without glue and print in two colours.

![Name Side](images/Name%20Side.jpg)

![Options Gallery](images/Options%20Gallery.jpg)

Settings for each game live in a small config file. The first is **The King's Dilemma**, where these holders are in use. To add another game, copy the config, measure your screen and change a few numbers.

## What it generates

| File | What it is |
|---|---|
| `base-closed-front.stl` | Base with screen slots, two rounded token trays and a ledge for a lid |
| `base-open-front.stl` | Same base with no player-side wall: the tray floors end in a low lip, so tilting the base forward pours the tokens out |
| `base-no-floor.stl` | Floorless variant: the trays are open to the table |
| `lid-plain.stl`, `lid-crown.stl`, `lid-handle.stl` | Lids for the closed-front base: flat, with a king's-crown grip, or with a bar grip |
| `plaques/<name>.3mf` | Name plaque: black plate with flush white text, printed face down. Two parts (plate + text) for an AMS / multi-material printer |
| `plaques-raised/<name>.3mf` | Variant: raised letters, printed face up |
| `plaques-small-white/<name>.3mf` | Variant: smaller white plaque with black text |

Every plaque `.3mf` loads as one object with two parts in Bambu Studio, OrcaSlicer or PrusaSlicer; assign a filament to each part. Separate STLs for each part are written too.

### Ready-to-print Bambu Studio projects

Each run also writes `bambu-studio/bases.3mf`, `lids.3mf` and `name-plates.3mf`: complete Bambu Studio projects with the print settings already set, so you can open them and print. They're built from the projects in [`templates/`](templates/), which are the ones the King's Dilemma set was printed with (Bambu Lab P1S, 0.4 mm nozzle, PLA Matte).

- **Name plaques:** one object per name, with the plate on filament 1 and the text on filament 2, laid out on a smooth PEI plate with the first-layer settings tuned for fine lettering. The layout leaves room for the prime tower where the template puts it. If they don't all fit, the rest go into `name-plates-2.3mf` and so on. Use **Arrange** if you prefer a different layout. If you change the purge settings and the tower grows into a plaque, drag the tower aside.
- **Lids:** the crown keeps its 0.12 mm detail layers.
- **Bases:** for a game with the same base shape as the template, the adaptive layer heights are kept. A different shape gets plain 0.2 mm layers; re-run *Adaptive* in Bambu Studio if you like.
- **Other printers:** if you use a different Bambu printer, Bambu Studio asks to switch the printer profile when you open the project. OrcaSlicer usually opens these projects too. Other slicers just load the models, so use the STLs and the settings below.

Skip the projects with `--no-projects`.

**Features**
- **Rounded tray floors:** slide a token towards you and it rides up the curve and tips up, so it's easy to pinch.
- **Glue-free plaques:** a dovetail rail on the base takes the plaque. Slide it down from above and it's held in place.
- **Uniform lettering:** every plaque uses the same letter size, shrunk automatically to fit the longest name.

## Quick start

```bash
pip install -r requirements.txt
python screen_holder.py games/kings-dilemma.toml
```

The files are written to `out/kings-dilemma/`. A run takes a few seconds.

The names are what change from group to group, so give them on the command line:

```bash
python screen_holder.py games/kings-dilemma.toml --names "Alice" "Bob" "Carol"   # your group's names
python screen_holder.py games/kings-dilemma.toml --names "Alice" --plaques-only  # just new plaques
python screen_holder.py games/kings-dilemma.toml --out my_folder                 # write somewhere else
```

## Game configs

Each game has a small settings file in [`games/`](games/), so you never need to edit the script. Each file holds that game's screen dimensions, base size and font. Plaque `names` are optional: put them in if the game has fixed player identities, like the Houses in The King's Dilemma. Otherwise leave them out and use `--names`, which always overrides the config.

```toml
name = "The King's Dilemma"

[screen]
centre = 127.0   # width of the centre panel (mm)
wing = 64.0      # width of each wing (mm)
angle = 60.0     # angle between the centre panel and each wing (degrees)
slot = 1.8       # screen thickness + about 0.3 mm

[base]
height = 25.0
depth = 63.0     # must be more than wing x sin(angle) + ~5 mm

[plaques]
names = ["House Delirea", "House Prunier", "House Adderbite", "House Rambam", "House Wulfric"]
font = "fonts/CinzelDecorative-Bold.ttf"

[fit]
plaque = 0.2     # plaque slide clearance per side
lid = 0.25       # lid clearance per side
```

**Fine-tuning:** any other setting can go in an optional `[advanced]` table, using the setting's name from the script (for example `FRONT_LIP_ANGLE = 40`). `python screen_holder.py --list-settings` shows them all.

**Typos are caught:** unknown keys stop the script with a message saying what's allowed.

### Adding your game

1. Copy `games/kings-dilemma.toml` to `games/<your-game>.toml`.
2. Measure your player screen:
   - **centre:** the width of the middle panel;
   - **wing:** the width of one side panel;
   - **angle:** how far the wings fold forward from the centre panel's line when the screen stands on the table;
   - **slot:** the screen's thickness plus about 0.3 mm.
3. Set `depth` so the wings fit. The script warns if they'd stick out past the front.
4. Run `python screen_holder.py games/<your-game>.toml` and check the result in your slicer.
5. Once you've printed it and it fits, **open a pull request** adding your config, so others can use it too.

**Or ask an AI agent:** give this repo to an AI coding assistant, such as Claude, and describe your screen. For example:

> "My player screen has a 140 mm centre panel and two 70 mm wings at 50°, and it's 2 mm thick. Make a config for it in games/, with the names Red, Blue, Green and Yellow, run it and give me the files."

**Current limitations:**
- Both wings must be the same width, which covers almost every player screen.
- There are always **two token trays** (see the ideas below).

## Ideas for future versions

Improvements noticed while using the King's Dilemma set. None are implemented yet; pull requests are very welcome.

- **A gentler tray slope.** The curved floor at the tray edges is steep enough that tokens sometimes get stuck instead of riding up for an easy pinch. A larger radius or a shallower ramp should help.
- **A configurable number of token trays**, instead of always two.
- **An option to close the finger notch** at the front of the closed-front base. It was meant for lifting a plain lid, but with the crown or bar grip it isn't needed and looks a bit out of place.

## Printing

The Bambu Studio projects already contain these settings. They were printed and tested on a Bambu Lab P1S (0.4 mm nozzle, PLA Matte), as part of the King's Dilemma set:

- **Bases** (upright, textured PEI): 0.2 mm layers (adaptive layer height makes the curved floors smoother), 2 walls, 15% infill. No supports.
- **Lids** (crown up / flat, textured PEI): 0.24 mm layers with a 0.24 mm first layer, so the 2.4 mm lid is exactly 10 layers. For the crown, add a height range modifier from 2.4 mm upwards at 0.12 mm.
- **Plaques** (face down, smooth PEI, AMS): 0.2 mm layers and first layer; the text is exactly 3 layers deep. First-layer line width 0.42 mm and first-layer infill speed 50 mm/s keep the letters crisp. The smooth plate gives a clean face; a textured plate shows through in the lettering.

| Tested in print | Generated but not test-printed |
|---|---|
| Closed-front and open-front bases, plain and crown lids, face-down plaques | Floorless base, bar-grip lid, raised and small white plaques |

Ready-to-print King's Dilemma files, with slicer profiles, are on MakerWorld.

## Credits

- **The base design is a remix** of ["King's Dilemma – Dashboard" by Frankenduke](https://www.printables.com/model/872481-kings-dilemma-dashboard), licensed CC BY 4.0. It reuses the concept (a trapezoid holder with screen slots and two token trays) and its key dimensions, which are the defaults here. If you publish models made with this script, please credit Frankenduke's original too.
- **The included font** is [Cinzel Decorative](https://fonts.google.com/specimen/Cinzel+Decorative) by Natanael Gama, under the SIL Open Font License 1.1 (`fonts/OFL.txt`).
- Built with [manifold3d](https://github.com/elalish/manifold), [trimesh](https://github.com/mikedh/trimesh), [shapely](https://github.com/shapely/shapely) and [matplotlib](https://matplotlib.org/).

## License

The code is released under the [MIT License](LICENSE): use it however you like, including commercially. The font keeps its own license (OFL).
