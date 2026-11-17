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

Events include `cast` and `death`, with `time`, `game_time`, `tick` and the unambiguous real `actor_slot`. Hero-targeted casts and hero deaths include `target_slot`. Casts include `button_key`. The combat log supplies their actual fractional timestamps. OpenDota's rounded display can differ by one second.

### Mid-lane spell evidence

The optional `facing_yaw` is the hero's networked body angle from `CBodyComponent.m_angRotation`, in degrees from positive world X toward positive world Y. It describes the sampled orientation and can be quantized. In match `9093297478`, 1,186 short movement segments with constant body yaw and 50–180 units of travel had a median absolute difference of 0.73° between displacement bearing and the sampled angle. Among them, 1,169 differed by less than 3°. A ground point near the edge of a facing-relative area needs a margin for angle quantization and movements during the cast.

In match `9094400691`, Shadow Fiend faced 158.90625° at 0:37.033. Outworld Destroyer was 27.7 units from the medium Shadowraze's projected center, 450 units in front of the caster. The combat log recorded that spell hitting Outworld Destroyer for 64 at 0:37.467. At 7:34.833 the death record explicitly credits Shadow Fiend's long Shadowraze with killing an enemy ranged creep. The hero can turn between a frame and the spell's impact. [Shadow Fiend match](https://www.opendota.com/matches/9094400691)

Each hero frame also carries the networked `level`, `hp`, `max_hp`, `attack_min`, `attack_max`, `attack_bonus` and `attack_range` when present. The corresponding ability record contains the rank learned by that hero at the sampled second. The `armor` and `magic_resist_pct` fields copy `m_flPhysicalArmorValue` and `m_flMagicalResistanceValue` respectively, with the latter expressed in percentage points. Additional modifiers, barriers and damage amplification can change the final applied damage.

From 0:00 through 10:00, frames include `mid_towers` with live mid T1 landmarks and `ranged_creeps` with live ordinary Radiant and Dire ranged lane creeps. A creep entry carries its serial-bearing entity handle as `id`, specific `name`, matching `team`, world coordinates, health, networked defenses, `takes_damage`, `unit_state` and `visible_to` for each team when available. The specific creep name comes from `m_iUnitNameStringTableIndex` in the replay's `UnitNames` table. The frame lists ordinary ranged creeps across the map. Mid-lane consumers must check their geometry against actual mid T1 landmarks and filter on team visibility.

During the same ten minutes, `spell_damage` records report a hero's logged damage to a named real hero or an ordinary ranged creep. They include the named inflictor in `button_key`, `damage`, and, when transmitted, `hp_after` and `damage_type`. A `ranged_last_hit` record comes directly from a hero-attributed combat-log death and includes the fatal inflictor only when the log names it. Compare the actor's team with `target_name` to distinguish last hits from denies. The combat log identifies the victim creep type but omits the serial-bearing entity handle. A row must therefore not be assigned to a particular photographed creep merely because the names match.

Against the previous reader, the two verified replays reproduced all 5,708 original hero frames and all 7,982 previously recorded cast and hero-death events. The additional first-ten-minute evidence contained 17,439 observations of ordinary ranged creeps in 1,202 frames. In match `9093297478` at 1:59.333, demo tick 9840, both the damage and death records attribute an enemy ranged creep to Queen of Pain's Scream of Pain. The damage event records 96 and zero remaining HP. In match `9088199723` a ranged-creep death at 3:56.100 explicitly names Rubick's Fade Bolt. [Queen of Pain match](https://www.opendota.com/matches/9093297478) · [Rubick match](https://www.opendota.com/matches/9088199723)

When Valve transmits `m_iCurrentCharges` for Smoke of Deceit, its button record includes the integer `charges`. A consumer checking whether smoke was usable can require a positive charge count alongside the inventory slot, activation state and cooldown. An omitted count is unknown. Other items retain their existing readiness rules.

From 0:00 through 8:00 inclusive, each frame also records `wave_units`. Every row gives an active, living unit's `id` (entity handle with serial), `kind` (`lane`, `neutral` or `tower`), `team`, and rounded world coordinates `x` and `y`. The tower rows include the internal `name` of a top or bottom tier-one or tier-two tower. The neutral rows require team 4. A sample exceeding the 256-unit cap is unavailable. The positions support replaying movement and measuring separation. Identifying a player's attack command or a creep's aggro target requires separate evidence.

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

In that same match, the frame immediately before Pudge's recorded Smoke of Deceit use at 680.433 seconds shows one charge in inventory slot 1, an active button and zero cooldown. The stack disappears by the next sampled frame. Hoodwink's recorded smoke use at 1069.733 seconds takes a stack from two charges to one, confirming the counter on a stack that survived activation. The neighbouring samples place that stack in the backpack, so its availability between the one-second samples is unknown.

During Rubick's Black Hole at approximately 967 seconds, Pudge has sufficient mana and Dismember ready, and Dire can see Rubick. The measured center distance is approximately 5,168 units, which rules out an immediate Dismember at that sampled position. [Pudge match](https://www.opendota.com/matches/9088199723), [Rubick match](https://www.opendota.com/matches/9093297478)
