def calculate_volume(length_m, breadth_m, height_m):
    """
    Calculates the volume of a rectangular granite block.
    
    Args:
        length_m (float): Length in metres.
        breadth_m (float): Breadth in metres.
        height_m (float): Height in metres.
        
    Returns:
        float: Volume in cubic metres (m3).
        
    Raises:
        ValueError: If any dimension is not a number or is not strictly positive.
    """
    for dim_name, dim_val in [("length", length_m), ("breadth", breadth_m), ("height", height_m)]:
        # Validate that the dimension is a number (int or float) and not a boolean
        if not isinstance(dim_val, (int, float)) or isinstance(dim_val, bool):
            raise ValueError(f"Invalid type for {dim_name}: must be a float or integer, got {type(dim_val).__name__}.")
        
        # Validate strictly positive
        if dim_val <= 0:
            raise ValueError(f"Invalid value for {dim_name}: {dim_val}. Dimensions must be strictly positive.")

    return float(length_m * breadth_m * height_m)


def calculate_weight(volume_m3, density_mt_per_m3=2.7):
    """
    Calculates the estimated weight of a granite block based on volume and density.
    
    Args:
        volume_m3 (float): Volume of the block in cubic metres.
        density_mt_per_m3 (float): Density in metric tonnes per cubic metre.
                                   NOTE: The default value of 2.7 MT/m3 is a POC PLACEHOLDER
                                   and is not the official government rate.
                                   
    Returns:
        float: Estimated weight in metric tonnes (MT).
        
    Raises:
        ValueError: If volume or density are not positive numbers.
    """
    for name, val in [("volume", volume_m3), ("density", density_mt_per_m3)]:
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise ValueError(f"Invalid type for {name}: must be a float or integer, got {type(val).__name__}.")
        if val <= 0:
            raise ValueError(f"Invalid value for {name}: {val}. Must be strictly positive.")
            
    return float(volume_m3 * density_mt_per_m3)
