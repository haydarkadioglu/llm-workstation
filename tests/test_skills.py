import pytest

from workstation.skills.generate import draft_skill
from workstation.skills.store import Skill, SkillStore, parse_skill, slugify


@pytest.fixture
def store(tmp_path):
    return SkillStore(tmp_path / "skills")


def sample(name="weekly-report"):
    return Skill(name, "Writes a weekly report.", "# Report\n1. List the wins.\n2. List the risks.")


def test_markdown_round_trip():
    skill = sample()
    assert parse_skill(skill.to_markdown()) == skill


def test_plain_markdown_is_accepted():
    skill = parse_skill("# Be kind\nAlways say please.", "manners.md")
    assert skill.description == "Be kind"
    assert "please" in skill.instructions


def test_unclosed_header_from_real_model_output():
    text = "---\nname: product_summary\ndescription: 'Makes a table.'\nrules:\n- Use a table.\n- End with a note."
    skill = parse_skill(text)
    assert skill.name == "product-summary"
    assert skill.description == "Makes a table."
    assert "- Use a table." in skill.instructions
    assert "name:" not in skill.instructions


def test_empty_skill_is_rejected():
    with pytest.raises(ValueError):
        parse_skill("---\nname: x\n---\n")


def test_slugify_blocks_paths():
    assert "/" not in slugify("../../etc/passwd")
    assert slugify("Haftalık Rapor!") == "haftal-k-rapor"


def test_saved_skill_survives_a_new_store(store):
    store.save(sample())
    fresh = SkillStore(store.root)
    assert fresh.get("weekly-report").description == "Writes a weekly report."
    assert fresh.describe_all()[0]["enabled"] is True


def test_invalid_name_cannot_reach_outside(store):
    with pytest.raises(ValueError):
        store.get("../secret")


def test_only_enabled_skills_reach_the_prompt(store):
    store.save(sample("one"))
    store.save(sample("two"))
    store.set_enabled("two", False)
    prompt = store.prompt(10000)
    assert "Skill: one" in prompt and "Skill: two" not in prompt


def test_prompt_is_empty_without_skills(store):
    assert store.prompt(1000) == ""


def test_prompt_respects_size_limit(store):
    store.save(Skill("big", "d", "x" * 5000))
    assert len(store.prompt(100)) == 100


def test_zip_moves_skills_to_another_machine(store, tmp_path):
    store.save(sample("one"))
    store.save(sample("two"))
    other = SkillStore(tmp_path / "elsewhere")
    assert sorted(s.name for s in other.import_zip(store.export_zip())) == ["one", "two"]
    assert other.get("two").instructions == sample().instructions


def test_zip_without_skills_is_rejected(store):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "hello")
    with pytest.raises(ValueError):
        store.import_zip(buffer.getvalue())


def test_delete_removes_file_and_flag(store):
    store.save(sample())
    assert store.delete("weekly-report")
    assert store.get("weekly-report") is None
    assert store.prompt(1000) == ""


def test_draft_parses_model_output(make_manager):
    manager = make_manager(["```markdown\n---\nname: Sea Poems\ndescription: Writes sea poems.\n---\n\n# Poems\n1. Rhyme.\n```"])
    skill = draft_skill(manager, "write poems about the sea")
    assert skill.name == "sea-poems"
    assert skill.description == "Writes sea poems."
    assert skill.instructions.startswith("# Poems")
    assert "```" not in skill.instructions


def test_draft_without_header_still_gives_a_skill(make_manager):
    manager = make_manager(["<think>hmm</think>Always answer in haiku."])
    skill = draft_skill(manager, "answer in haiku")
    assert skill.instructions == "Always answer in haiku."
    assert skill.name == "answer-in-haiku"


def test_refinement_sends_the_current_skill(make_manager):
    manager = make_manager(["---\nname: a\ndescription: b\n---\nc"])
    skill = draft_skill(manager, "make it shorter", "OLD SKILL TEXT", "keep-this-name")
    assert "OLD SKILL TEXT" in manager.backend.prompts[0][1]["content"]
    assert skill.name == "keep-this-name"
