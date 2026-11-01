# API reference

The everyday entry points are `rsi.improve`, `rsi.recommend` and `rsi.transfer_report`; a custom problem is a
`Domain` (or a `FunctionDomain`) over a `TaskSuite`. Each method's own `Config` and `run` live in `rsi.<method>`.

## Running a method

::: rsi.api.improve

::: rsi.api.recommend

::: rsi.core.run.transfer_report

::: rsi.core.run.Budget

::: rsi.core.run.ImprovementResult

## Defining a problem

::: rsi.core.domain.Domain

::: rsi.core.domain.FunctionDomain

::: rsi.core.domain.Execution

::: rsi.core.tasks.Task

::: rsi.core.tasks.TaskSuite

::: rsi.core.artifact.Artifact

## Measuring

::: rsi.core.evaluate.Evaluator

::: rsi.core.evaluate.EvalResult

## Models

::: rsi.core.llm.get_llm
