idf_component_register(
    SRCS "main.c" "gc9d01.c" "pca9685.c"
    INCLUDE_DIRS "."
    REQUIRES driver nvs_flash
)
