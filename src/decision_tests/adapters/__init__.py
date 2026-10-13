from decision_tests.adapters.azure import AzureAdapter
from decision_tests.adapters.jev import JevAdapter
from decision_tests.adapters.openai import OpenAIAdapter

ADAPTERS = {"jev": JevAdapter, "openai": OpenAIAdapter, "azure": AzureAdapter}
