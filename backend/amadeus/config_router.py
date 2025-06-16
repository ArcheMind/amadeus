from typing import Dict, Any, Optional, List, Callable
import copy
from urllib.parse import quote, unquote
from fastapi import APIRouter, HTTPException, Response
from jsonschema import validate, exceptions as jsonschema_exceptions
from loguru import logger


def create_dynamic_enum_enhancer():
    """
    Create a schema enhancer that resolves dynamic enums.
    """

    async def resolve_dynamic_enums_recursive(
        schema_node: Dict[str, Any],
        current_config_data: Dict[str, Any],
        instance_name: Optional[str] = None,
    ):
        if isinstance(schema_node, dict):
            if "$dynamicEnum" in schema_node:
                dynamic_enum_config = schema_node["$dynamicEnum"]
                source_key = dynamic_enum_config.get("source")
                value_field = dynamic_enum_config.get("valueField")
                name_field = dynamic_enum_config.get("nameField", value_field)
                enum_filter = dynamic_enum_config.get("filter")

                if source_key and value_field and source_key in current_config_data:
                    source_data = current_config_data[source_key]
                    if isinstance(source_data, list):
                        enum_values = []
                        enum_names = []
                        for item in source_data:
                            if isinstance(item, dict):
                                if enum_filter:
                                    filter_field = enum_filter.get("field")
                                    filter_value = enum_filter.get("value")
                                    if not (
                                        filter_field
                                        and filter_field in item
                                        and item[filter_field] == filter_value
                                    ):
                                        continue

                                if value_field in item:
                                    enum_values.append(item[value_field])
                                    if name_field in item:
                                        enum_names.append(item[name_field])
                                    else:
                                        enum_names.append(item[value_field])

                        schema_node["enum"] = enum_values
                        schema_node["enumNames"] = enum_names
                        del schema_node["$dynamicEnum"]

            for value in schema_node.values():
                await resolve_dynamic_enums_recursive(
                    value, current_config_data, instance_name
                )

        elif isinstance(schema_node, list):
            for item in schema_node:
                await resolve_dynamic_enums_recursive(
                    item, current_config_data, instance_name
                )

    async def enhance_schema(
        schema: Dict[str, Any],
        config_data: Dict[str, Any],
        class_name: str,
        instance_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Enhance schema by resolving dynamic enums."""
        if "schema" in schema:
            await resolve_dynamic_enums_recursive(
                schema["schema"], config_data, instance_name
            )
        return schema

    return enhance_schema


class ConfigRouter:
    def __init__(
        self,
        config_schema: Dict[str, Any],
        data_getter,
        data_setter,
    ):
        """
        Initialize the ConfigRouter.

        Args:
            config_schema: The configuration schema definition
            data_getter: Function that returns the current configuration data
            data_setter: Function that updates the configuration data
        """
        self.config_schema = config_schema
        self._data_getter = data_getter
        self._data_setter = data_setter
        self._schema_enhancers: Dict[str, List[Callable]] = {}
        self.router = APIRouter(prefix="/config")

        # Register the dynamic enum enhancer for all classes
        dynamic_enum_enhancer = create_dynamic_enum_enhancer()
        for class_name in self.config_schema.keys():
            self.register_schema_enhancer(class_name, dynamic_enum_enhancer)

        self._setup_routes()

    @property
    def config_data(self) -> Dict[str, Any]:
        """Get the current configuration data using the data getter."""
        return self._data_getter()

    async def _update_config_data(self, new_data: Dict[str, Any]):
        """Update the configuration data using the data setter."""
        changed = await self._data_setter(new_data)
        return not changed

    def register_schema_enhancer(self, class_name: str, enhancer):
        """
        Register a schema enhancer for a specific configuration class.

        Args:
            class_name: The name of the configuration class
            enhancer: A function that takes (schema, config_data, instance_name) and returns enhanced schema
        """
        if class_name not in self._schema_enhancers:
            self._schema_enhancers[class_name] = []
        self._schema_enhancers[class_name].append(enhancer)

    async def _get_class_schema(
        self, class_name: str, instance_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get schema for a configuration class, optionally enhanced with instance data.

        The schema goes through multiple enhancement stages:
        1. Basic schema resolution (dynamic enums)
        2. Registered schema enhancers
        3. Instance-specific enhancements (if instance_name is provided)
        """
        if class_name not in self.config_schema:
            raise HTTPException(
                status_code=404, detail=f"Configuration class '{class_name}' not found"
            )

        # Get base schema
        schema = copy.deepcopy(self.config_schema[class_name])

        if "schema" in schema:
            # Apply registered enhancers
            if class_name in self._schema_enhancers:
                for enhancer in self._schema_enhancers[class_name]:
                    schema = await enhancer(
                        schema,
                        self.config_data,
                        class_name,
                        instance_name,
                    )

        return schema["schema"]

    def _fill_defaults_recursive(self, schema_node: Dict[str, Any], data_node: Any):
        """Recursively fill data with default values from the schema."""
        if not isinstance(data_node, dict) or "properties" not in schema_node:
            return

        for prop, prop_schema in schema_node["properties"].items():
            if "default" in prop_schema and prop not in data_node:
                data_node[prop] = copy.deepcopy(prop_schema["default"])

            if prop in data_node:
                if prop_schema.get("type") == "object":
                    self._fill_defaults_recursive(prop_schema, data_node[prop])
                elif (
                    prop_schema.get("type") == "array"
                    and "items" in prop_schema
                    and isinstance(data_node[prop], list)
                ):
                    item_schema = prop_schema["items"]
                    if item_schema.get("type") == "object":
                        for item_data in data_node[prop]:
                            self._fill_defaults_recursive(item_schema, item_data)

    async def _validate_instance(
        self,
        class_name: str,
        instance_data: Dict[str, Any],
        schema: Optional[Dict[str, Any]] = None,
    ):
        """Validate instance data against schema."""
        if schema is None:
            schema = await self._get_class_schema(class_name)

        if not schema:
            raise HTTPException(
                status_code=400,
                detail=f"No schema definition found for class: '{class_name}'",
            )

        try:
            self._fill_defaults_recursive(schema, instance_data)
            validate(instance=instance_data, schema=schema)
        except jsonschema_exceptions.ValidationError as e:
            raise HTTPException(
                status_code=400, detail=f"Validation error: {e.message}"
            )
        except jsonschema_exceptions.SchemaError as e:
            logger.error(f"Schema error: {e.message}")
            raise HTTPException(status_code=500, detail=f"Schema error: {e.message}")

    def _setup_routes(self):
        """Setup all the routes for the configuration API."""

        @self.router.get("/class")
        async def get_classes():
            """Get list of all configuration classes."""
            classes = []
            for class_name, config in self.config_schema.items():
                class_data = config.copy()
                class_data["name"] = class_name
                class_data["isSingleton"] = not class_data.pop("isList", False)
                classes.append(class_data)
            return classes

        @self.router.get("/class/{class_name}/schema")
        async def get_class_schema(class_name: str, instance: Optional[str] = None):
            """Get schema for a configuration class."""
            if instance:
                instance = unquote(instance)
            return await self._get_class_schema(class_name, instance)

        @self.router.get("/class/{class_name}/instances")
        async def get_instances(class_name: str):
            """Get all instances of a configuration class."""
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if not self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is not a list type",
                )

            return self.config_data.get(class_name, [])

        @self.router.get("/class/{class_name}/instances/{instance_name}")
        async def get_instance(class_name: str, instance_name: str):
            """Get a single instance of a configuration class."""
            instance_name = unquote(instance_name)
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if not self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is not a list type",
                )

            # Get current data
            current_data = self.config_data

            # Find and return instance
            existing_instances = current_data.get(class_name, [])
            for instance in existing_instances:
                if instance.get("name") == instance_name:
                    return instance

            raise HTTPException(
                status_code=404, detail=f"Instance '{instance_name}' not found"
            )

        @self.router.post("/class/{class_name}/instances", status_code=201)
        async def create_instance(
            class_name: str, instance_data: Dict[str, Any], response: Response
        ):
            """Create a new instance of a configuration class."""
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if not self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is not a list type",
                )

            schema = await self._get_class_schema(class_name)
            self._fill_defaults_recursive(schema, instance_data)
            await self._validate_instance(class_name, instance_data, schema=schema)

            if "name" not in instance_data:
                raise HTTPException(
                    status_code=400, detail="Instance must have a 'name' field"
                )

            # Get current data
            current_data = self.config_data

            # Check for duplicate names
            existing_instances = current_data.get(class_name, [])
            if any(
                instance.get("name") == instance_data["name"]
                for instance in existing_instances
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"Instance with name '{instance_data['name']}' already exists",
                )

            # Add new instance
            if class_name not in current_data:
                current_data[class_name] = []
            current_data[class_name].append(instance_data)

            # Update data
            await self._update_config_data(current_data)

            encoded_name = quote(instance_data["name"])
            response.headers["Location"] = (
                f"/config/class/{class_name}/instances/{encoded_name}"
            )
            return instance_data

        @self.router.put("/class/{class_name}/instances/{instance_name}")
        async def update_instance(
            class_name: str, instance_name: str, instance_data: Dict[str, Any]
        ):
            """Update an existing instance of a configuration class."""
            instance_name = unquote(instance_name)
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if not self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is not a list type",
                )

            schema = await self._get_class_schema(class_name, instance_name)
            self._fill_defaults_recursive(schema, instance_data)
            await self._validate_instance(class_name, instance_data, schema=schema)

            if instance_data.get("name") != instance_name:
                raise HTTPException(
                    status_code=400,
                    detail="Instance name in URL must match name in data",
                )

            # Get current data
            current_data = self.config_data

            # Find and update instance
            existing_instances = current_data.get(class_name, [])
            for i, instance in enumerate(existing_instances):
                if instance.get("name") == instance_name:
                    current_data[class_name][i] = instance_data
                    if await self._update_config_data(current_data):
                        return instance_data
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Update of instance '{instance_name}' failed",
                        )

            raise HTTPException(
                status_code=404, detail=f"Instance '{instance_name}' not found"
            )

        @self.router.delete("/class/{class_name}/instances/{instance_name}")
        async def delete_instance(class_name: str, instance_name: str):
            """Delete an instance of a configuration class."""
            instance_name = unquote(instance_name)
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if not self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is not a list type",
                )

            # Get current data
            current_data = self.config_data

            # Find and remove instance
            existing_instances = current_data.get(class_name, [])
            for i, instance in enumerate(existing_instances):
                if instance.get("name") == instance_name:
                    del current_data[class_name][i]
                    if await self._update_config_data(current_data):
                        return {"status": "success"}
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Deletion of instance '{instance_name}' failed",
                        )

            raise HTTPException(
                status_code=404, detail=f"Instance '{instance_name}' not found"
            )

        @self.router.get("/class/{class_name}/singleton")
        async def get_singleton(class_name: str):
            """Get a singleton configuration class."""
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is a list type",
                )

            return self.config_data.get(class_name, {})

        @self.router.put("/class/{class_name}/singleton")
        async def update_singleton(class_name: str, data: Dict[str, Any]):
            """Update a singleton configuration class."""
            if class_name not in self.config_schema:
                raise HTTPException(
                    status_code=404,
                    detail=f"Configuration class '{class_name}' not found",
                )

            if self.config_schema[class_name].get("isList", False):
                raise HTTPException(
                    status_code=400,
                    detail=f"Configuration class '{class_name}' is a list type",
                )

            schema = await self._get_class_schema(class_name)
            self._fill_defaults_recursive(schema, data)
            await self._validate_instance(class_name, data, schema=schema)

            # Get current data and update
            current_data = self.config_data
            current_data[class_name] = data
            if await self._update_config_data(current_data):
                return data
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Update of singleton '{class_name}' failed",
                )
