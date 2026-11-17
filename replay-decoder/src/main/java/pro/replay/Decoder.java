package pro.replay;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import skadistats.clarity.Clarity;
import skadistats.clarity.event.Insert;
import skadistats.clarity.model.CombatLogEntry;
import skadistats.clarity.model.Entity;
import skadistats.clarity.model.StringTable;
import skadistats.clarity.model.Vector;
import skadistats.clarity.processor.entities.Entities;
import skadistats.clarity.processor.entities.OnEntityCreated;
import skadistats.clarity.processor.entities.UsesEntities;
import skadistats.clarity.processor.gameevents.OnCombatLogEntry;
import skadistats.clarity.processor.reader.OnMessage;
import skadistats.clarity.processor.reader.OnTickEnd;
import skadistats.clarity.processor.runner.Context;
import skadistats.clarity.processor.runner.SimpleRunner;
import skadistats.clarity.processor.stringtables.StringTables;
import skadistats.clarity.processor.stringtables.UsesStringTable;
import skadistats.clarity.source.MappedFileSource;
import skadistats.clarity.wire.shared.common.proto.CommonNetworkBaseTypes.CNETMsg_Tick;

import java.io.BufferedWriter;
import java.io.IOException;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Sample Source 2 entity state, preserving the distinction between missing data and zero. */
@UsesEntities
@UsesStringTable("EntityNames")
public final class Decoder {
    private static final String RULES = "m_pGameRules.";
    private static final String[] INDEX = new String[256];
    static {
        for (int i = 0; i < INDEX.length; i++) INDEX[i] = "%04d".formatted(i);
    }
    private static final Gson JSON = new Gson();
    @Insert private Entities entities;
    @Insert private StringTables tables;

    private final BufferedWriter out;
    private final List<JsonObject> events = new ArrayList<>();
    private final Map<String, Integer> slots = new HashMap<>();
    private final Set<String> ambiguousNames = new HashSet<>();
    private Entity rules, resource, radiant, dire;
    private int serverTick = -1;
    private int lastSecond = -1;

    private Decoder(BufferedWriter out) { this.out = out; }

    @OnEntityCreated
    public void created(Entity entity) {
        switch (entity.getDtClass().getDtName()) {
            case "CDOTAGamerulesProxy" -> rules = entity;
            case "CDOTA_PlayerResource" -> resource = entity;
            case "CDOTA_DataRadiant" -> radiant = entity;
            case "CDOTA_DataDire" -> dire = entity;
            default -> { }
        }
    }

    @OnMessage(CNETMsg_Tick.class)
    public void serverClock(CNETMsg_Tick message) { serverTick = message.getTick(); }

    private static Object value(Entity entity, String name) {
        if (entity == null || !entity.isActive()) return null;
        var path = entity.getFieldPathForName(name);
        return path == null ? null : entity.getPropertyForFieldPath(path);
    }

    private static Number number(Entity entity, String name) {
        return value(entity, name) instanceof Number n && Double.isFinite(n.doubleValue()) ? n : null;
    }

    private static void copyNumber(Entity entity, String prop, JsonObject to, String key) {
        Number n = number(entity, prop);
        if (n != null) to.addProperty(key, n);
    }

    private static void copyBool(Entity entity, String prop, JsonObject to, String key) {
        if (value(entity, prop) instanceof Boolean b) to.addProperty(key, b);
    }

    private Entity handle(Entity owner, String prop) {
        Number handle = number(owner, prop);
        if (handle == null) return null;
        Entity entity = entities.getByHandle(handle.intValue());
        return entity != null && entity.isActive() ? entity : null;
    }

    private String name(Entity entity) {
        return tableName(entity, "m_pEntity.m_nameStringTableIndex", "EntityNames");
    }

    private String tableName(Entity entity, String field, String table) {
        Number id = number(entity, field);
        StringTable names = tables.forName(table);
        if (id == null || names == null || id.intValue() < 0 || id.intValue() >= names.getEntryCount()) return null;
        return names.getNameByIndex(id.intValue());
    }

    private Double engineTime(Context ctx) {
        Number direct = number(rules, RULES + "m_fGameTime");
        if (direct != null) return direct.doubleValue();
        Number pausedTicks = number(rules, RULES + "m_nTotalPausedTicks");
        Object paused = value(rules, RULES + "m_bGamePaused");
        if (serverTick < 0 || pausedTicks == null || !(paused instanceof Boolean)) return null;
        Number tick = Boolean.TRUE.equals(paused) ? number(rules, RULES + "m_nPauseStartTick") : Integer.valueOf(serverTick);
        double interval = ctx.getMillisPerTick() / 1000.0;
        if (tick == null || !Double.isFinite(interval) || interval <= 0) return null;
        return (tick.longValue() - pausedTicks.longValue()) * interval;
    }

    private static void coordinate(Entity hero, String axis, JsonObject record, String key) {
        Number cell = number(hero, "CBodyComponent.m_cell" + axis);
        Number offset = number(hero, "CBodyComponent.m_vec" + axis);
        if (cell != null && offset != null) record.addProperty(key, (cell.intValue() - 128) * 128.0 + offset.doubleValue());
    }

    private static void visibility(Entity team, Entity hero, String key, JsonObject result) {
        int index = hero.getIndex(), word = index >>> 6;
        if (word >= INDEX.length) return;
        Object bits = value(team, "m_bNPCVisibleState." + INDEX[word]);
        // Each Source 2 visibility word is a uint64, including bits 32..63.
        if (bits instanceof Long mask) result.addProperty(key, (mask & (1L << (index & 63))) != 0);
    }

    private void defenses(Entity unit, JsonObject result) {
        copyNumber(unit, "m_iHealth", result, "hp");
        copyNumber(unit, "m_iMaxHealth", result, "max_hp");
        copyNumber(unit, "m_flPhysicalArmorValue", result, "armor");
        copyNumber(unit, "m_flMagicalResistanceValue", result, "magic_resist_pct");
        copyBool(unit, "m_bTakesDamage", result, "takes_damage");
        Number flags = number(unit, "m_nUnitState64");
        if (flags != null) result.addProperty("unit_state", new BigInteger(Long.toUnsignedString(flags.longValue())));
        JsonObject sight = new JsonObject();
        visibility(radiant, unit, "2", sight);
        visibility(dire, unit, "3", sight);
        result.add("visible_to", sight);
    }

    private static boolean ordinaryRanged(String unit) {
        return "npc_dota_creep_goodguys_ranged".equals(unit) || "npc_dota_creep_badguys_ranged".equals(unit);
    }

    @UsesStringTable("UnitNames")
    private void midTargets(JsonObject frame) {
        JsonArray creeps = new JsonArray(), towers = new JsonArray();
        var active = entities.stream().filter(Entity::isActive).iterator();
        while (active.hasNext()) {
            Entity unit = active.next();
            String cls = unit.getDtClass().getDtName();
            boolean creep = cls.equals("CDOTA_BaseNPC_Creep_Lane");
            if (!creep && !cls.equals("CDOTA_BaseNPC_Tower")) continue;
            // EntityNames identifies the generic lane-creep class. Only UnitNames
            // distinguishes an ordinary ranged creep from melee, flag or super.
            String target = creep ? tableName(unit, "m_iUnitNameStringTableIndex", "UnitNames") : name(unit);
            if (creep ? !ordinaryRanged(target) :
                    !("dota_goodguys_tower1_mid".equals(target) || "dota_badguys_tower1_mid".equals(target))) continue;
            Number team = number(unit, "m_iTeamNum"), life = number(unit, "m_lifeState"), hp = number(unit, "m_iHealth");
            if (team == null || team.intValue() != (target.contains("_goodguys_") ? 2 : 3)
                    || life == null || life.intValue() != 0 || hp == null || hp.intValue() <= 0) continue;
            JsonObject row = new JsonObject();
            row.addProperty("id", unit.getHandle());
            row.addProperty("team", team.intValue());
            row.addProperty("name", target);
            coordinate(unit, "X", row, "x");
            coordinate(unit, "Y", row, "y");
            if (creep) defenses(unit, row);
            (creep ? creeps : towers).add(row);
        }
        frame.add("ranged_creeps", creeps);
        frame.add("mid_towers", towers);
    }

    private void button(Entity hero, boolean isItem, int index, double now, double horn,
                        JsonObject buttons, Set<String> ambiguous) {
        Entity entity = handle(hero, (isItem ? "m_hItems." : "m_vecAbilities.") + INDEX[index]);
        if (entity == null) return;
        String key = name(entity);
        if (key == null || key.isBlank() || key.startsWith("special_bonus_") || key.startsWith("generic_hidden")) return;
        if (isItem) {
            if (!key.startsWith("item_") || key.startsWith("item_recipe_")) return;
            key = key.substring(5);
        }
        if (ambiguous.contains(key)) return;
        JsonObject b = new JsonObject();
        b.addProperty("kind", isItem ? "item" : "ability");
        b.addProperty("slot", index);
        // Non-consumables can report zero here even when ready to cast.
        if (isItem && key.equals("smoke_of_deceit"))
            copyNumber(entity, "m_iCurrentCharges", b, "charges");
        if (!isItem && key.equals("sniper_shrapnel"))
            copyNumber(entity, "m_nAbilityCurrentCharges", b, "charges");
        if (!isItem && key.equals("pudge_rot"))
            copyBool(entity, "m_bToggleState", b, "toggled");
        copyNumber(entity, "m_iLevel", b, "level");
        copyNumber(entity, "m_iManaCost", b, "mana_cost");
        copyBool(entity, "m_bInAbilityPhase", b, "in_phase");
        copyBool(entity, "m_bInIndefiniteCooldown", b, "indefinite");
        copyBool(entity, "m_bFrozenCooldown", b, "frozen");
        Number cd = number(entity, "m_fCooldown");
        // In the Source 2 replay this field decrements each server tick.
        // Tested against actual Black Hole and Dismember casts and cooldown end.
        if (cd != null) b.addProperty("cooldown", Math.max(0.0, cd.doubleValue()));
        Number channel = number(entity, "m_flChannelStartTime");
        if (channel != null) {
            boolean running = channel.doubleValue() > 0 && channel.doubleValue() <= now + 0.05;
            b.addProperty("channeling", running);
            if (running) b.addProperty("channel_start", channel.doubleValue() - horn);
        }
        Object hidden = value(entity, "m_bHidden"), activated = value(entity, "m_bActivated");
        if (hidden instanceof Boolean h && activated instanceof Boolean a) {
            boolean enabled = a && !h;
            if (isItem) {
                // m_bItemEnabled stays false even for recorded Blink/Glimmer
                // activations. The equipped handle, m_bActivated and the
                // backpack re-enable time are the relevant network evidence.
                Number enableTime = number(entity, "m_flEnableTime");
                if (index >= 6 || enableTime != null && enableTime.doubleValue() > now) {
                    enabled = false;
                } else if (enableTime == null) {
                    if (enabled) { addButton(key, b, buttons, ambiguous); return; }
                }
            }
            b.addProperty("active", enabled);
        }
        addButton(key, b, buttons, ambiguous);
    }

    private static void addButton(String key, JsonObject b, JsonObject buttons, Set<String> ambiguous) {
        JsonObject previous = buttons.getAsJsonObject(key);
        // Stolen spells and duplicate items can coexist: prefer the only active
        // copy. If both are active their cooldowns cannot be identified by name.
        if (previous != null) {
            boolean wasActive = previous.has("active") && previous.get("active").getAsBoolean();
            boolean isActive = b.has("active") && b.get("active").getAsBoolean();
            if (wasActive && !isActive) return;
            if (wasActive == isActive) {
                buttons.remove(key);
                ambiguous.add(key);
                return;
            }
        }
        buttons.add(key, b);
    }

    private JsonObject heroState(Entity hero, String unit, int heroID, double now, double horn) {
        JsonObject result = new JsonObject();
        result.addProperty("name", unit);
        result.addProperty("hero_id", heroID);
        coordinate(hero, "X", result, "x");
        coordinate(hero, "Y", result, "y");
        Object rotation = value(hero, "CBodyComponent.m_angRotation");
        if (rotation instanceof Vector angles && angles.getDimension() >= 2) {
            double yaw = angles.getElement(1);
            if (Double.isFinite(yaw)) result.addProperty("facing_yaw", (yaw % 360.0 + 360.0) % 360.0);
        }
        Number life = number(hero, "m_lifeState"), hp = number(hero, "m_iHealth");
        if (life != null && hp != null) result.addProperty("alive", life.intValue() == 0 && hp.intValue() > 0);
        copyNumber(hero, "m_flMana", result, "mana");
        if (now - horn < 601) {
            copyNumber(hero, "m_flMaxMana", result, "max_mana");
            copyNumber(hero, "m_flAgilityTotal", result, "agility_total");
            copyNumber(hero, "m_flIntellectTotal", result, "intellect_total");
            copyNumber(hero, "m_flStrengthTotal", result, "strength_total");
            Number lastAttack = number(hero, "m_flLastAttackTime");
            if (lastAttack != null && lastAttack.doubleValue() > 0 && lastAttack.doubleValue() <= now)
                result.addProperty("last_attack_time", lastAttack.doubleValue() - horn);
        }
        copyNumber(hero, "m_iCurrentLevel", result, "level");
        copyNumber(hero, "m_iDamageMin", result, "attack_min");
        copyNumber(hero, "m_iDamageMax", result, "attack_max");
        copyNumber(hero, "m_iDamageBonus", result, "attack_bonus");
        copyNumber(hero, "m_iAttackRange", result, "attack_range");
        defenses(hero, result);
        JsonObject buttons = new JsonObject();
        Set<String> ambiguous = new HashSet<>();
        for (int i = 0; i < 32; i++) button(hero, false, i, now, horn, buttons, ambiguous);
        for (int i = 0; i < 9; i++) button(hero, true, i, now, horn, buttons, ambiguous);
        result.add("buttons", buttons);
        return result;
    }

    @OnTickEnd
    public void tick(Context ctx, boolean synthetic) throws IOException {
        if (synthetic || resource == null || rules == null) return;
        Number horn = number(rules, RULES + "m_flGameStartTime");
        Number end = number(rules, RULES + "m_flGameEndTime");
        if (horn == null || horn.doubleValue() <= 0) return;
        Double now = engineTime(ctx);
        if (now == null || end != null && end.doubleValue() > 0 && now > end.doubleValue()) return;
        double elapsed = now - horn.doubleValue();
        int second = (int) Math.floor(elapsed);
        if (second < 0 || second <= lastSecond) return;
        JsonObject heroes = new JsonObject();
        Set<String> duplicates = new HashSet<>();
        for (int id = 0; id < 24; id++) {
            String fields = "m_vecPlayerTeamData." + INDEX[id] + ".";
            Number team = number(resource, "m_vecPlayerData." + INDEX[id] + ".m_iPlayerTeam");
            Number sideSlot = number(resource, fields + "m_iTeamSlot");
            Number heroID = number(resource, fields + "m_nSelectedHeroID");
            if (team == null || sideSlot == null || heroID == null ||
                (team.intValue() != 2 && team.intValue() != 3) || sideSlot.intValue() < 0 || sideSlot.intValue() > 4) continue;
            Entity hero = handle(resource, fields + "m_hSelectedHero");
            if (hero == null || !hero.getDtClass().getDtName().startsWith("CDOTA_Unit_Hero_")) continue;
            String unit = name(hero);
            if (unit == null || !unit.startsWith("npc_dota_hero_")) continue;
            int slot = sideSlot.intValue() + (team.intValue() == 2 ? 0 : 128);
            Integer previous = slots.putIfAbsent(unit, slot);
            if (previous != null && previous != slot) ambiguousNames.add(unit);
            String key = Integer.toString(slot);
            if (heroes.has(key)) duplicates.add(key);
            heroes.add(key, heroState(hero, unit, heroID.intValue(), now, horn.doubleValue()));
        }
        duplicates.forEach(heroes::remove);
        if (heroes.size() == 0) return;
        JsonObject frame = new JsonObject();
        frame.addProperty("time", second);
        frame.addProperty("game_time", elapsed);
        frame.addProperty("tick", ctx.getTick());
        frame.add("heroes", heroes);
        if (second <= 480) frame.add("wave_units", WaveTracker.sample(entities, tables));
        if (second <= 600) midTargets(frame);
        if (lastSecond >= 0) out.write(",\n");
        out.write(JSON.toJson(frame));
        lastSecond = second;
    }

    @OnCombatLogEntry
    public void combat(Context ctx, CombatLogEntry log) {
        String kind = switch (log.getType()) {
            case DOTA_COMBATLOG_DEATH -> "death";
            case DOTA_COMBATLOG_ABILITY, DOTA_COMBATLOG_ITEM -> "cast";
            case DOTA_COMBATLOG_DAMAGE -> "spell_damage";
            default -> null;
        };
        if (kind == null || !log.hasTimestamp() || !log.hasAttackerName() || log.isAttackerIllusion()) return;
        Number horn = number(rules, RULES + "m_flGameStartTime");
        if (horn == null || horn.doubleValue() <= 0) return;
        String source = log.getAttackerName();
        Integer actor = slots.get(source);
        if (actor == null || ambiguousNames.contains(source)) return;
        String victim = log.hasTargetName() ? log.getTargetName() : null;
        Integer target = !log.isTargetIllusion() && !ambiguousNames.contains(victim) ? slots.get(victim) : null;
        double when = log.getTimestamp() - horn.doubleValue();
        if (!Double.isFinite(when) || when < 0) return;
        if (kind.equals("death") && target == null) {
            if (when > 600 || log.isTargetIllusion() || !ordinaryRanged(victim)) return;
            kind = "ranged_last_hit";
        }
        if (kind.equals("spell_damage") && (when > 600 || log.isTargetIllusion()
                || target == null && !ordinaryRanged(victim) || !log.hasValue() || log.getValue() <= 0)) return;
        if (kind.equals("spell_damage") && !log.hasInflictorName()) {
            // The log classifies ordinary direct hits under damage category 1.
            // Reject unattributed damage, illusions and proxy attackers.
            if (target == null || !log.hasDamageCategory() || log.getDamageCategory() != 1 ||
                    !log.hasDamageType() || log.getDamageType() != 1 || !log.hasDamageSourceName() ||
                    !source.equals(log.getDamageSourceName())) return;
            kind = "attack_damage";
        }
        JsonObject e = new JsonObject();
        e.addProperty("kind", kind);
        e.addProperty("time", (int) Math.floor(when));
        e.addProperty("game_time", when);
        e.addProperty("tick", ctx.getTick());
        e.addProperty("actor_slot", actor);
        if (target != null) e.addProperty("target_slot", target);
        if (kind.equals("cast") || kind.equals("spell_damage")) {
            if (!log.hasInflictorName()) return;
            String button = log.getInflictorName();
            if (button == null || button.isBlank() || button.equals("dota_unknown")) return;
            e.addProperty("button_key", button.startsWith("item_") ? button.substring(5) : button);
        }
        if (kind.equals("spell_damage") || kind.equals("attack_damage")) {
            e.addProperty("target_name", victim);
            if (log.hasDamageSourceName())
                e.addProperty("direct_hero_source", source.equals(log.getDamageSourceName()));
            e.addProperty("damage", log.getValue());
            if (log.hasHealth()) e.addProperty("hp_after", log.getHealth());
            if (log.hasDamageType()) e.addProperty("damage_type", log.getDamageType());
        }
        if (kind.equals("ranged_last_hit")) {
            e.addProperty("target_name", victim);
            String inflictor = log.hasInflictorName() ? log.getInflictorName() : null;
            if (inflictor != null && !inflictor.isBlank() && !inflictor.equals("dota_unknown"))
                e.addProperty("button_key", inflictor.startsWith("item_") ? inflictor.substring(5) : inflictor);
        }
        events.add(e);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("Usage: replay-decoder <match.dem> <facts.json>");
        var info = Clarity.infoForFile(args[0]).getGameInfo().getDota();
        if (!info.hasMatchId() || info.getMatchId() <= 0) throw new IllegalArgumentException("Missing Dota match ID");
        try (var source = new MappedFileSource(args[0]); var writer = Files.newBufferedWriter(Path.of(args[1]))) {
            writer.write("{\"format\":\"pro-replay-truth-v1\",\"match_id\":" + info.getMatchId() + ",\"frames\":[\n");
            Decoder decoder = new Decoder(writer);
            new SimpleRunner(source).runWith(decoder);
            if (decoder.lastSecond < 0) throw new IOException("No timestamped Source 2 hero state");
            writer.write("\n],\"events\":[\n");
            for (int i = 0; i < decoder.events.size(); i++) {
                if (i > 0) writer.write(",\n");
                writer.write(JSON.toJson(decoder.events.get(i)));
            }
            writer.write("\n]}\n");
        }
    }
}
