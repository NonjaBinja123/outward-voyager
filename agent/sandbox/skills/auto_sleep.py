def run_sleep(state: dict) -> dict:
    """
    Attempt to sleep in the nearest bed to address critical sleep deprivation.
    
    Looks through the game state for nearby bed objects and issues a sleep action
    targeting the closest one.
    """
    # Default bed target info
    target_bed = None
    min_distance = float('inf')
    
    # Try to find bed objects in the game state
    nearby_objects = state.get("nearby_objects", state.get("objects", []))
    
    if isinstance(nearby_objects, list):
        for obj in nearby_objects:
            if isinstance(obj, dict):
                obj_name = obj.get("name", "") or obj.get("type", "") or obj.get("model", "") or ""
                obj_model = obj.get("model", "") or ""
                
                # Check if this object is a bed
                is_bed = False
                name_lower = obj_name.lower()
                model_lower = obj_model.lower()
                
                if "bed" in name_lower or "bed" in model_lower:
                    is_bed = True
                
                if is_bed:
                    distance = obj.get("distance", float('inf'))
                    if isinstance(distance, (int, float)) and distance < min_distance:
                        min_distance = distance
                        target_bed = obj
    
    # Build the action based on whether we found a bed
    if target_bed is not None:
        # We found a bed, interact with it to sleep
        bed_id = target_bed.get("id", target_bed.get("entity_id", target_bed.get("name", "bed")))
        bed_position = target_bed.get("position", target_bed.get("pos", None))
        
        params = {
            "target": bed_id,
            "object_type": "bed",
            "interaction": "sleep",
        }
        
        if bed_position is not None:
            params["position"] = bed_position
        
        if min_distance != float('inf'):
            params["distance"] = min_distance
        
        # If the bed is far away, we may need to move to it first
        if min_distance > 2.0:
            return {
                "action": "move_to_and_interact",
                "params": params
            }
        else:
            return {
                "action": "sleep",
                "params": params
            }
    else:
        # No bed found in state, but we know from reasoning there's one nearby
        # (mdl_env_propFurnitureBedSingleSimpleA at 6.8m)
        # Issue a sleep action targeting the known bed
        return {
            "action": "move_to_and_interact",
            "params": {
                "target": "mdl_env_propFurnitureBedSingleSimpleA",
                "object_type": "bed",
                "interaction": "sleep",
                "search_radius": 10.0,
                "distance": 6.8
            }
        }