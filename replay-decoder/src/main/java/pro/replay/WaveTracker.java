package pro.replay;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import skadistats.clarity.model.Entity;
import skadistats.clarity.processor.entities.Entities;
import skadistats.clarity.processor.stringtables.StringTables;

/** Snapshot ordinary live lane creeps, uncontrolled neutrals and side-lane tower landmarks. */
final class WaveTracker {
    private WaveTracker() {}

    private static Number number(Entity entity, String name) {
        var path = entity.getFieldPathForName(name);
        if (path == null) return null;
        Object value = entity.getPropertyForFieldPath(path);
        return value instanceof Number n && Double.isFinite(n.doubleValue()) ? n : null;
    }

    private static Integer coordinate(Entity entity, String suffix) {
        Number cell = number(entity, "CBodyComponent.m_cell" + suffix);
        Number offset = number(entity, "CBodyComponent.m_vec" + suffix);
        if (cell == null || offset == null) return null;
        return (int) Math.round((cell.intValue() - 128) * 128.0 + offset.doubleValue());
    }

    static JsonElement sample(Entities entities, StringTables tables) {
        JsonArray units = new JsonArray();
        var names = tables.forName("EntityNames");
        var iterator = entities.stream().filter(Entity::isActive).iterator();
        while (iterator.hasNext()) {
            Entity unit = iterator.next();
            String kind = switch (unit.getDtClass().getDtName()) {
                case "CDOTA_BaseNPC_Creep_Lane" -> "lane";
                case "CDOTA_BaseNPC_Creep_Neutral" -> "neutral";
                case "CDOTA_BaseNPC_Tower" -> "tower";
                default -> null;
            };
            if (kind == null) continue;
            Number team = number(unit, "m_iTeamNum");
            Number life = number(unit, "m_lifeState"), hp = number(unit, "m_iHealth");
            if (team == null || life == null || hp == null || life.intValue() != 0 || hp.intValue() <= 0) continue;
            if (kind.equals("neutral") ? team.intValue() != 4 : team.intValue() != 2 && team.intValue() != 3) continue;
            JsonObject row = new JsonObject();
            if (kind.equals("tower")) {
                Number index = number(unit, "m_pEntity.m_nameStringTableIndex");
                if (index == null || names == null || index.intValue() < 0 || index.intValue() >= names.getEntryCount()) continue;
                String name = names.getNameByIndex(index.intValue());
                if (name == null || !(name.contains("_tower1_") || name.contains("_tower2_"))
                        || !(name.endsWith("_top") || name.endsWith("_bot"))) continue;
                row.addProperty("name", name);
            }
            Integer x = coordinate(unit, "X"), y = coordinate(unit, "Y");
            if (x == null || y == null) continue;
            row.addProperty("id", unit.getHandle());  // Includes serial; does not alias a recycled entity index.
            row.addProperty("kind", kind);
            row.addProperty("team", team.intValue());
            row.addProperty("x", x);
            row.addProperty("y", y);
            units.add(row);
            if (units.size() > 256) return JsonNull.INSTANCE;
        }
        return units;
    }
}
