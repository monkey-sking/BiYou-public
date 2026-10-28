def build_registry(*adapters):
    return {adapter.platform_name: adapter for adapter in adapters}
