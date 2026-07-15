# Semantic Conversion Guide For Agents

Convert each extracted Blue Book record into structured semantic rule records.

Input files live in:

`work/chapter_inputs/<chapter>.json`

Output files must be written to:

`data/semantic_rules/<chapter>.json`

Use the schema in:

`data/semantic_rule_schema.json`

## Conversion Rules

- Preserve every source `rule_id`.
- Do not summarize multiple rules into one record unless the input record is itself a table/list rule.
- Convert prose into predicate/action language.
- Use chemistry-neutral predicate names where a graph predicate will later be implemented.
- Put compiler or implementation needs in `implementation_requirements`, not in hidden assumptions.
- Use `source_quote` as a short supporting quote from the source body, not the whole source body.
- Use `compare_by` for ordered preference criteria.
- Use `exceptions` for "except", "unless", "however", "not used when", and correction overrides.
- Preserve cross-reference context in `depends_on`. If the rule says "see P-...",
  "according to P-...", "as described in P-...", or imports criteria from another
  rule, put that referenced rule id in `depends_on`.
- Keep outputs valid JSON.

## Preferred Predicate Style

Use phrases that can later become functions, for example:

- `molecule_has_parent_hydride_candidate`
- `candidate_contains_maximum_number_of_senior_characteristic_groups`
- `numbering_gives_lowest_locant_set_to_suffix_group`
- `candidate_name_uses_retained_name_allowed_for_substitution`

## Output Envelope

```json
{
  "source": "https://iupac.qmul.ac.uk/BlueBook/",
  "source_version": "IUPAC Blue Book 2013 online version plus post-V3 web corrections",
  "chapter": "P-1",
  "conversion_status": "draft_semantic",
  "records": []
}
```
