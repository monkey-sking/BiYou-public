from abc import ABC, abstractmethod
from typing import Optional, List

class LLMBase(ABC):
    """Abstract Base Class for LLM Services"""

    @abstractmethod
    async def generate_text(self, prompt: str, model: str = None) -> str:
        """
        Generate text response from a prompt.
        
        Args:
            prompt: Text prompt
            model: Optional model identifier (e.g. 'Thinking', 'Pro')
            
        Returns:
            Generated text content
        """
        pass

    @abstractmethod
    async def generate_image(self, prompt: str, input_image_path: str, output_path: str, model: str = None) -> Optional[str]:
        """
        Generate/Edit image based on prompt and input image.
        
        Args:
            prompt: Text prompt for image generation/editing
            input_image_path: Path to source image
            output_path: Path to save the generated image
            model: Optional model identifier
            
        Returns:
            Path to saved image if successful, None otherwise
        """
        pass
