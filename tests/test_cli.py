from click.testing import CliRunner
from ais_shader.cli import cli

def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "trajectory" in result.output
    assert "trajectorize" in result.output
    assert "generate-lines" in result.output
    assert "generate-segments" in result.output

def test_trajectory_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["trajectory", "--help"])
    assert result.exit_code == 0
    assert "compute" in result.output
    assert "to-linestring" in result.output
    assert "to-segment" in result.output

def test_trajectory_compute_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["trajectory", "compute", "--help"])
    assert result.exit_code == 0
    assert "--epoch-time" in result.output

def test_trajectory_to_segment_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["trajectory", "to-segment", "--help"])
    assert result.exit_code == 0
    assert "--epoch-time" in result.output
