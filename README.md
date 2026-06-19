Elastodyn sensors are in main_sensorlist.txt
Blade sensors are manully injected
sensorlist_parts contains beamdyn sensors
config - get_blade_config()
main - 
'''
 if convention_hint == 'EXPLICIT':
        print(f"  → Convention override: EXPLICIT (Reading sensors directly from sensorList.txt)")
        convention = 'EXPLICIT'
        bld_stations = blade_cfg['stations']
        n_blades = blade_cfg['n_blades']
    else:
        # Fallback to legacy auto-detection
        convention, bld_stations, n_blades = detect_blade_sensors(sensor_cols)
        if convention_hint != 'AUTO' and convention_hint in ('ED', 'BD'):
            print(f"  → Convention override: {convention_hint}")
'''
