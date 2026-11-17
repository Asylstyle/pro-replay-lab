# Replay state decoder

This component reads a Valve Source 2 `.dem` using Clarity 5.0.1 and emits the actual entity state sampled once per game second. It requires Java 21 or newer. The Python coach downloads the pinned runtime dependencies on first use. Maven is needed to rebuild the Java component.

```powershell
mvn -f replay-decoder/pom.xml clean package
java -Xmx768m -jar replay-decoder/target/replay-decoder.jar data/match.dem data/match-facts.json
```

The parser validates the embedded Dota match ID. The Python pipeline validates the requested ID, keeps the raw replay and extracted facts in its local cache, and handles failed extraction. Replay files and client match data stay outside version control.

## Output

The UTF-8 JSON has `format` equal to `pro-replay-truth-v1`, `match_id`, `frames` and `events`. Each frame and event occupies one line, allowing streaming reads.

Each frame contains `time` as the integer second since the horn, `game_time` as the sampled fractional second, the demo `tick`, and a `heroes` object keyed by the OpenDota player slot. Player slots are resolved through the canonical selected hero handle in `CDOTA_PlayerResource`, its team and team slot.

Each hero contains the canonical `name`, `hero_id`, world coordinates `x` and `y`, `alive`, `mana`, the unsigned `unit_state`, and `visible_to` for team 2 and team 3. Its `buttons` object is keyed by internal ability or item name. Item names omit the `item_` prefix. Button fields include `kind`, `slot`, `level`, `mana_cost`, `cooldown`, `active`, `in_phase`, `indefinite`, `frozen` and `channeling`. An ongoing channel includes its `channel_start`. Missing fields remain absent.

Events have `kind` equal to `cast` or `death`, `time`, `game_time`, `tick` and the unambiguous real `actor_slot`. Hero-targeted casts and hero deaths include `target_slot`. Casts include `button_key`. The combat log supplies their actual fractional timestamps. OpenDota's rounded display can differ by one second.

## Evidence interpretation

- The engine clock uses `m_fGameTime` where available. Current Source 2 replays use the server tick, `m_nTotalPausedTicks`, `m_nPauseStartTick` and the recorded tick interval. Subtracting `m_flGameStartTime` gives the match clock.
- The cooldown field `m_fCooldown` is the remaining duration in the verified replays. It decreases each server tick. This was checked against actual casts and the following frames.
- Ability availability uses `m_bActivated` and `m_bHidden`. Items additionally need an equipped handle in main slots 0–5 and an elapsed `m_flEnableTime`. The networked `m_bItemEnabled` stays false for recorded successful casts in these replays and is excluded from availability checks.
- Team visibility comes from `CDOTA_DataRadiant` and `CDOTA_DataDire` using `m_bNPCVisibleState`. Each word has 64 bits. The word index is the hero entity index divided by 64, and its bit index is the remainder. This represents visibility available to the team. A player's camera position and attention require additional evidence.
- Coordinates combine `CBodyComponent.m_cellX/Y` and `m_vecX/Y`. The coordinate unit supports center-to-center distance calculations. The verification layer applies the supported ability's cast rules and handles missing range evidence.
- `m_flChannelStartTime` changes from zero to the engine time when a channel begins and returns to zero when it ends. This was checked on both Enigma's and Rubick's Black Hole.

The underlying APIs and state definitions are available in the upstream projects. [Clarity](https://github.com/skadistats/clarity), [OpenDota clock and position extraction](https://github.com/odota/parser/blob/master/src/main/java/opendota/Parse.java), [Valve network schema](https://github.com/SteamTracking/GameTracking-Dota2/blob/master/DumpSource2/schemas/server/CDOTABaseAbility.h), [Team visibility extraction](https://github.com/whanyu1212/gem-dota/blob/main/src/gem/extractors/visibility.py)

## Verification on real matches

Match `9088199723` yielded 2,917 frames with all ten heroes, 4,230 casts and 102 hero-versus-hero deaths. Match `9093297478` yielded 2,791 complete frames, 3,578 casts and 72 hero-versus-hero deaths.

In the first match, Pudge cast Dismember at 471.700 seconds. The frame at 471 seconds shows 310 mana and zero cooldown. At 472 seconds it shows 212 mana and 29.70 seconds remaining, then 28.70 seconds at 473. Pudge's Glimmer cast at 1710.667 seconds similarly changes mana from 562 to 439 and cooldown from zero to 14.70, then 13.70 seconds.

During Rubick's Black Hole at approximately 967 seconds, Pudge has sufficient mana and Dismember ready, and Dire can see Rubick. The measured center distance is approximately 5,168 units, which rules out an immediate Dismember at that sampled position. [Pudge match](https://www.opendota.com/matches/9088199723), [Rubick match](https://www.opendota.com/matches/9093297478)
