# Repository Map Methodology for LLMs

## Overview

The repository map is a concise representation of a git repository's structure and key components, designed to provide efficient context for Large Language Models (LLMs) when working with codebases.

## Key Components

1. **File List**: A list of all files in the repository.
2. **Symbol Definitions**: Key symbols (classes, methods, functions) defined in each file.
3. **Critical Code Lines**: The most important lines of code for each symbol definition.

## Methodology

### 1. Parse Source Code

Use tree-sitter to parse the source code into an Abstract Syntax Tree (AST).

- Utilize the `py-tree-sitter-languages` Python module for language support.
- Parse each file in the repository to generate its AST.

### 2. Extract Symbol Definitions

Traverse the AST to identify and extract important symbol definitions:

- Classes
- Methods
- Functions
- Variables
- Types

### 3. Identify Symbol References

Analyze the AST to find where symbols are used or referenced throughout the codebase.

### 4. Rank Symbol Importance

Create a graph where:
- Nodes represent source files
- Edges connect files with dependencies

Use a graph ranking algorithm to determine the most important symbols based on their usage and references.

### 5. Generate Concise Map

For each file, include:
- File path
- Most important symbol definitions (based on ranking)
- Critical lines of code for each included symbol

### 6. Optimize for Token Budget

- Allow for a configurable token budget (default: 1000 tokens)
- Select the most important parts of the codebase that fit within the token budget

## Example Output

```plaintext
aider/coders/base_coder.py:
⋮...
│class Coder:
│    abs_fnames = None
⋮...
│    @classmethod
│    def create(
│        self,
│        main_model,
│        edit_format,
│        io,
│        skip_model_availabily_check=False,
│        **kwargs,
⋮...
│    def abs_root_path(self, path):
⋮...
│    def run(self, with_message=None):
⋮...

aider/commands.py:
⋮...
│class Commands:
│    voice = None
│
⋮...
│    def get_commands(self):
⋮...
│    def get_command_completions(self, cmd_name, partial):
⋮...
│    def run(self, inp):
⋮...
```

## Implementation Notes

1. Use tree-sitter queries to extract relevant information from the AST.
2. Modify the `tags.scm` files from various tree-sitter language implementations to customize symbol extraction.
3. Implement a ranking system to determine the most important symbols and code lines.
4. Create a function to generate the map string, respecting the token budget.
5. Ensure the output is formatted consistently and is easy for LLMs to parse and understand.