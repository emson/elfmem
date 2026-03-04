create a claude team to implement the rest of the functionality for this library. Take everything you have learned in the exploration and playground, look at the
  coding_principles.md testing_principles.md and the prompt_ab_testing.md files to understand our principles.
  Think hard about how professional open source developers would create a project like this, and follow those principles and approaches.
  Create these teams:
  lead-dev-team (opus): this team deeply understands the requirements, and all the documents about this project, they are professional python developers who have built many high
  value open source python projects. They know the best practices, they find edge cases and implement fixes or mitigations, they deeply understand the secuirity vectors and how
  to build a secure and robust python software library. They write the feature plans (./docs/plans/ ) and tell the dev-team how to implement them. They answer questions the
  dev-team will have, and guide them to a best practice, robust, flexible and elegant solution. The lead-dev-team doesn't write the code, but they write the specs (plans) and
  help the dev-team implement it. The lead-dev-team will work with the testing-team to help them write the most effective tests for the dev-team to implement.

  dev-team (sonnet): this team is a hightly experienced Python open source development team. They deeply understand how to build production grade Python libraries. They deeply
  understand all aspects of memory systems (like mem0, etc) they follow best practice development approaches, they think outside the box to find robust, flexible and elegant
  solutions. They work closely with the lead-dev-team, and ask questions to ensure they deeply understand what they are building, and how to build it. The dev-team works with
  the testing-team, and once tests have been created will go ahead and implement the best code. They will also refactor and improve the code to keep it DRY and elegant.

  testing-team (haiku): this team writes the key tests for the system based off the lead-dev-team plan (in ./docs/plans ). They follow the testing_principles.md document, and
  focus on the core tests, and focus on simple well crafted and effective tests. They avoid complexity and err on the side of writing less tests, but making them very good and
  focused on the core functionality of the system. The testing-team write the tests before the dev-team implements them, and they inform the dev-team when to 