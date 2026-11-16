// Covers that keep the front-door wall full before the catalog is. A fresh
// install, or the private beta before its first sync, has few or no games,
// and a wall with gaps (or none at all) loses the whole look. These are only
// decoration: well-known Steam games whose portrait art Steam serves at the
// same address as every other cover. They are never shown as catalog
// entries and never open a game page.
const WALL_APPS = [
  [1245620, 'Elden Ring'], [1086940, "Baldur's Gate 3"], [1091500, 'Cyberpunk 2077'], [292030, 'The Witcher 3'],
  [1174180, 'Red Dead Redemption 2'], [367520, 'Hollow Knight'], [1145360, 'Hades'], [413150, 'Stardew Valley'],
  [814380, 'Sekiro'], [374320, 'Dark Souls III'], [1593500, 'God of War'], [2050650, 'Resident Evil 4'],
  [1817070, "Marvel's Spider-Man Remastered"], [105600, 'Terraria'], [2358720, 'Black Myth: Wukong'], [553850, 'Helldivers 2'],
  [588650, 'Dead Cells'], [504230, 'Celeste'], [620, 'Portal 2'], [1868140, 'Dave the Diver'],
  [2379780, 'Balatro'], [1145350, 'Hades II'], [271590, 'Grand Theft Auto V'], [990080, 'Hogwarts Legacy'],
  [1888930, 'The Last of Us Part I'], [1196590, 'Resident Evil Village'], [239140, 'Dying Light'], [1172470, 'Apex Legends'],
];
export const WALL_FILLER = WALL_APPS.map(([steam_appid, name]) => ({ id: `wall-${steam_appid}`, steam_appid, name }));

// The catalog's own games first, topped up from the filler (skipping any app
// the catalog already has) until the wall has `size` covers.
export function wallGames(catalog, size = 27) {
  const games = catalog.filter(Boolean).slice(0, size);
  const have = new Set(games.map((game) => game.steam_appid));
  for (const filler of WALL_FILLER) {
    if (games.length >= size) break;
    if (!have.has(filler.steam_appid)) games.push(filler);
  }
  return games;
}
