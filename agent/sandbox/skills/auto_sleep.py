def run_sleep(state: dict) -> dict:
    bed_object_name = "mdl_env_propFurnitureBedSingleSimpleA"
    
    bed_entity = None
    entities = state.get("entities", [])
    if isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                model = entity.get("model", "") or entity.get("name", "") or entity.get("type", "")
                if bed_object_name in str(model):
                    bed_entity = entity
                    break
    
    if not bed_entity and isinstance(entities, list):
        for entity in entities:
            if isinstance(entity, dict):
                for key, value in entity.items():
                    if bed_object_name in str(value):
                        bed_entity = entity
                        break
            if bed_entity:
                break
    
    params = {
        "object": bed_object_name,
        "duration": "full",
    }
    
    if bed_entity:
        entity_id = bed_entity.get("id") or bed_entity.get("entity_id")
        if entity_id is not None:
            params["target_id"] = entity_id
        
        position = bed_entity.get("position") or bed_entity.get("pos") or bed_entity.get("location")
        if position is not None:
            params["target_position"] = position
    
    return {
        "action": "sleep",
        "params": params
    }