from abc import ABC, abstractmethod

class ConversionStrategy(ABC):
    @abstractmethod
    def execute(self, processor):
        pass