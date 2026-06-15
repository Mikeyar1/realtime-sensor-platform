import dataclasses
import json

from config_mgr import config

mapped_values_start_index = config["mapped_values_start_index"]
unmapped_json_index = config["unmapped_json_index"]


@dataclasses.dataclass
class ReplaySample:
    row: list[any]
    is_mapped: bool
    is_published: bool = False

    @staticmethod
    def get_mapped_sample(row: list[any]) -> list[any]:
        return row[mapped_values_start_index:]

    @staticmethod
    def get_unmapped_sample(row: list[any]) -> list[any]:
        json_database = row[unmapped_json_index:]
        json_database_0 = json_database[0]
        json_parsed = json.loads(json_database_0)
        first_values_list = [d["value"] for d in json_parsed]
        return first_values_list

    def get_sample(self) -> list[any]:
        if self.is_mapped:
            return ReplaySample.get_mapped_sample(self.row)
        else:
            return ReplaySample.get_unmapped_sample(self.row)
