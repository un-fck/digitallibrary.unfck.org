import { query } from "../db/db";

export interface EntityOption {
  entity: string;
  entity_long?: string;
}

// Fetch entities from systemchart.entities (shared UN entities table)
// Customize this query for your data source if needed
export async function fetchEntities(): Promise<EntityOption[]> {
  const rows = await query<{ entity: string; entity_long: string | null }>(
    `SELECT entity, entity_long FROM systemchart.entities ORDER BY entity`,
  );
  return rows.map((r) => ({
    entity: r.entity,
    entity_long: r.entity_long || undefined,
  }));
}
