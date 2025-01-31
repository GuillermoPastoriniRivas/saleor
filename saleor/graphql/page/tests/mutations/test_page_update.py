from datetime import date
from unittest import mock

import graphene
import pytest
from django.utils.functional import SimpleLazyObject
from django.utils.text import slugify
from freezegun import freeze_time

from .....attribute.models import AttributeValue
from .....attribute.utils import associate_attribute_values_to_instance
from .....page.error_codes import PageErrorCode
from .....page.models import Page
from .....tests.utils import dummy_editorjs
from .....webhook.event_types import WebhookEventAsyncType
from .....webhook.payloads import generate_page_payload
from ....tests.utils import get_graphql_content

UPDATE_PAGE_MUTATION = """
    mutation updatePage(
        $id: ID!, $input: PageInput!
    ) {
        pageUpdate(
            id: $id, input: $input
        ) {
            page {
                id
                title
                slug
                isPublished
                publicationDate
                attributes {
                    attribute {
                        slug
                    }
                    values {
                        slug
                        name
                        reference
                        file {
                            url
                            contentType
                        }
                    }
                }
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_update_page(staff_api_client, permission_manage_pages, page):
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    tag_attr = page_type.page_attributes.get(name="tag")
    tag_attr_id = graphene.Node.to_global_id("Attribute", tag_attr.id)
    new_value = "Rainbow"

    page_title = page.title
    new_slug = "new-slug"
    assert new_slug != page.slug

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {
            "slug": new_slug,
            "isPublished": True,
            "attributes": [{"id": tag_attr_id, "values": [new_value]}],
        },
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]["title"] == page_title
    assert data["page"]["slug"] == new_slug

    expected_attributes = []
    page_attr = page.attributes.all()
    for attr in page_type.page_attributes.all():
        if attr.slug != tag_attr.slug:
            values = [
                {"slug": slug, "file": None, "name": name, "reference": None}
                for slug, name in page_attr.filter(
                    assignment__attribute=attr
                ).values_list("values__slug", "values__name")
            ]
        else:
            values = [
                {
                    "slug": slugify(new_value),
                    "file": None,
                    "name": new_value,
                    "reference": None,
                }
            ]
        attr_data = {
            "attribute": {"slug": attr.slug},
            "values": values,
        }
        expected_attributes.append(attr_data)

    attributes = data["page"]["attributes"]
    assert len(attributes) == len(expected_attributes)
    for attr_data in attributes:
        assert attr_data in expected_attributes


@mock.patch("saleor.plugins.webhook.plugin.get_webhooks_for_event")
@mock.patch("saleor.plugins.webhook.plugin.trigger_webhooks_async")
@freeze_time("2020-03-18 12:00:00")
def test_update_page_trigger_webhook(
    mocked_webhook_trigger,
    mocked_get_webhooks_for_event,
    any_webhook,
    staff_api_client,
    permission_manage_pages,
    page,
    settings,
):
    query = UPDATE_PAGE_MUTATION

    settings.PLUGINS = ["saleor.plugins.webhook.plugin.WebhookPlugin"]
    mocked_get_webhooks_for_event.return_value = [any_webhook]

    page_title = page.title
    new_slug = "new-slug"
    assert new_slug != page.slug

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {
            "slug": new_slug,
            "isPublished": True,
        },
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]["title"] == page_title
    assert data["page"]["slug"] == new_slug
    page.publication_date = date(2020, 3, 18)
    expected_data = generate_page_payload(page, staff_api_client.user)
    mocked_webhook_trigger.assert_called_once_with(
        expected_data,
        WebhookEventAsyncType.PAGE_UPDATED,
        [any_webhook],
        page,
        SimpleLazyObject(lambda: staff_api_client.user),
    )


def test_update_page_only_title(staff_api_client, permission_manage_pages, page):
    """Ensures that updating page field without providing attributes is allowed."""
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    page_title = page.title
    new_slug = "new-slug"
    assert new_slug != page.slug

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {
            "slug": new_slug,
            "isPublished": True,
        },
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]["title"] == page_title
    assert data["page"]["slug"] == new_slug

    expected_attributes = []
    page_attr = page.attributes.all()
    for attr in page_type.page_attributes.all():
        values = [
            {"slug": slug, "file": None, "name": name, "reference": None}
            for slug, name in page_attr.filter(assignment__attribute=attr).values_list(
                "values__slug", "values__name"
            )
        ]
        attr_data = {
            "attribute": {"slug": attr.slug},
            "values": values,
        }
        expected_attributes.append(attr_data)

    attributes = data["page"]["attributes"]
    assert len(attributes) == len(expected_attributes)
    for attr_data in attributes:
        assert attr_data in expected_attributes


def test_update_page_with_file_attribute_value(
    staff_api_client, permission_manage_pages, page, page_file_attribute
):
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    page_type.page_attributes.add(page_file_attribute)
    new_value = "test.txt"
    page_file_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_file_attribute.pk
    )

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": page_file_attribute_id, "file": new_value}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_file_attribute.slug},
        "values": [
            {
                "slug": slugify(new_value),
                "name": new_value,
                "reference": None,
                "file": {
                    "url": "http://testserver/media/" + new_value,
                    "contentType": None,
                },
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]


def test_update_page_with_file_attribute_new_value_is_not_created(
    staff_api_client, permission_manage_pages, page, page_file_attribute
):
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    page_type.page_attributes.add(page_file_attribute)
    page_file_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_file_attribute.pk
    )
    existing_value = page_file_attribute.values.first()
    associate_attribute_values_to_instance(page, page_file_attribute, existing_value)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {
            "attributes": [
                {"id": page_file_attribute_id, "file": existing_value.file_url}
            ]
        },
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_file_attribute.slug},
        "values": [
            {
                "slug": existing_value.slug,
                "name": existing_value.name,
                "reference": None,
                "file": {
                    "url": f"http://testserver/media/{existing_value.file_url}",
                    "contentType": existing_value.content_type,
                },
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]


def test_update_page_clear_values(staff_api_client, permission_manage_pages, page):
    # given
    query = UPDATE_PAGE_MUTATION

    page_attr = page.attributes.first()
    attribute = page_attr.assignment.attribute
    attribute.value_required = False
    attribute.save(update_fields=["value_required"])

    page_file_attribute_id = graphene.Node.to_global_id("Attribute", attribute.pk)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": page_file_attribute_id, "file": ""}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    assert not data["page"]["attributes"][0]["values"]

    with pytest.raises(page_attr._meta.model.DoesNotExist):
        page_attr.refresh_from_db()


def test_update_page_with_page_reference_attribute_new_value(
    staff_api_client,
    permission_manage_pages,
    page_list,
    page_type_page_reference_attribute,
):
    # given
    query = UPDATE_PAGE_MUTATION

    page = page_list[0]
    ref_page = page_list[1]
    page_type = page.page_type
    page_type.page_attributes.add(page_type_page_reference_attribute)

    values_count = page_type_page_reference_attribute.values.count()
    ref_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_type_page_reference_attribute.pk
    )
    reference = graphene.Node.to_global_id("Page", ref_page.pk)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": ref_attribute_id, "references": [reference]}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_type_page_reference_attribute.slug},
        "values": [
            {
                "slug": f"{page.pk}_{ref_page.pk}",
                "name": page.title,
                "file": None,
                "reference": reference,
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]

    page_type_page_reference_attribute.refresh_from_db()
    assert page_type_page_reference_attribute.values.count() == values_count + 1


def test_update_page_with_page_reference_attribute_existing_value(
    staff_api_client,
    permission_manage_pages,
    page_list,
    page_type_page_reference_attribute,
):
    # given
    query = UPDATE_PAGE_MUTATION

    page = page_list[0]
    ref_page = page_list[1]
    page_type = page.page_type
    page_type.page_attributes.add(page_type_page_reference_attribute)

    attr_value = AttributeValue.objects.create(
        attribute=page_type_page_reference_attribute,
        name=page.title,
        slug=f"{page.pk}_{ref_page.pk}",
        reference_page=ref_page,
    )
    associate_attribute_values_to_instance(
        page, page_type_page_reference_attribute, attr_value
    )

    values_count = page_type_page_reference_attribute.values.count()
    ref_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_type_page_reference_attribute.pk
    )
    reference = graphene.Node.to_global_id("Page", ref_page.pk)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": ref_attribute_id, "references": [reference]}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_type_page_reference_attribute.slug},
        "values": [
            {
                "slug": attr_value.slug,
                "file": None,
                "name": page.title,
                "reference": reference,
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]

    page_type_page_reference_attribute.refresh_from_db()
    assert page_type_page_reference_attribute.values.count() == values_count


def test_update_page_with_product_reference_attribute_new_value(
    staff_api_client,
    permission_manage_pages,
    page,
    page_type_product_reference_attribute,
    product,
):
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    page_type.page_attributes.add(page_type_product_reference_attribute)

    values_count = page_type_product_reference_attribute.values.count()
    ref_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_type_product_reference_attribute.pk
    )
    reference = graphene.Node.to_global_id("Product", product.pk)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": ref_attribute_id, "references": [reference]}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_type_product_reference_attribute.slug},
        "values": [
            {
                "slug": f"{page.pk}_{product.pk}",
                "name": product.name,
                "file": None,
                "reference": reference,
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]

    page_type_product_reference_attribute.refresh_from_db()
    assert page_type_product_reference_attribute.values.count() == values_count + 1


def test_update_page_with_product_reference_attribute_existing_value(
    staff_api_client,
    permission_manage_pages,
    page,
    page_type_product_reference_attribute,
    product,
):
    # given
    query = UPDATE_PAGE_MUTATION

    page_type = page.page_type
    page_type.page_attributes.add(page_type_product_reference_attribute)

    attr_value = AttributeValue.objects.create(
        attribute=page_type_product_reference_attribute,
        name=page.title,
        slug=f"{page.pk}_{product.pk}",
        reference_product=product,
    )
    associate_attribute_values_to_instance(
        page, page_type_product_reference_attribute, attr_value
    )

    values_count = page_type_product_reference_attribute.values.count()
    ref_attribute_id = graphene.Node.to_global_id(
        "Attribute", page_type_product_reference_attribute.pk
    )
    reference = graphene.Node.to_global_id("Product", product.pk)

    page_id = graphene.Node.to_global_id("Page", page.id)

    variables = {
        "id": page_id,
        "input": {"attributes": [{"id": ref_attribute_id, "references": [reference]}]},
    }

    # when
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]
    updated_attribute = {
        "attribute": {"slug": page_type_product_reference_attribute.slug},
        "values": [
            {
                "slug": attr_value.slug,
                "file": None,
                "name": page.title,
                "reference": reference,
            }
        ],
    }
    assert updated_attribute in data["page"]["attributes"]

    page_type_product_reference_attribute.refresh_from_db()
    assert page_type_product_reference_attribute.values.count() == values_count


@freeze_time("2020-03-18 12:00:00")
def test_public_page_sets_publication_date(
    staff_api_client, permission_manage_pages, page_type
):
    data = {
        "slug": "test-url",
        "title": "Test page",
        "content": dummy_editorjs("Content for page 1"),
        "is_published": False,
        "page_type": page_type,
    }
    page = Page.objects.create(**data)
    page_id = graphene.Node.to_global_id("Page", page.id)
    variables = {"id": page_id, "input": {"isPublished": True, "slug": page.slug}}
    response = staff_api_client.post_graphql(
        UPDATE_PAGE_MUTATION, variables, permissions=[permission_manage_pages]
    )
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]

    assert not data["errors"]
    assert data["page"]["isPublished"] is True
    assert data["page"]["publicationDate"] == "2020-03-18"


@pytest.mark.parametrize("slug_value", [None, ""])
def test_update_page_blank_slug_value(
    staff_api_client, permission_manage_pages, page, slug_value
):
    query = UPDATE_PAGE_MUTATION
    assert slug_value != page.slug

    page_id = graphene.Node.to_global_id("Page", page.id)
    variables = {"id": page_id, "input": {"slug": slug_value, "isPublished": True}}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )
    content = get_graphql_content(response)
    errors = content["data"]["pageUpdate"]["errors"]

    assert len(errors) == 1
    assert errors[0]["field"] == "slug"
    assert errors[0]["code"] == PageErrorCode.REQUIRED.name


@pytest.mark.parametrize("slug_value", [None, ""])
def test_update_page_with_title_value_and_without_slug_value(
    staff_api_client, permission_manage_pages, page, slug_value
):
    query = """
        mutation updatePage($id: ID!, $title: String, $slug: String) {
        pageUpdate(id: $id, input: {title: $title, slug: $slug}) {
            page {
                id
                title
                slug
            }
            errors {
                field
                code
                message
            }
        }
    }
    """
    page_id = graphene.Node.to_global_id("Page", page.id)
    variables = {"id": page_id, "title": "test", "slug": slug_value}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_pages]
    )
    content = get_graphql_content(response)
    errors = content["data"]["pageUpdate"]["errors"]

    assert len(errors) == 1
    assert errors[0]["field"] == "slug"
    assert errors[0]["code"] == PageErrorCode.REQUIRED.name


UPDATE_PAGE_ATTRIBUTES_MUTATION = """
    mutation updatePage(
        $id: ID!, $input: PageInput!
    ) {
        pageUpdate(
            id: $id, input: $input
        ) {
            page {
                id
                title
                slug
                attributes {
                    attribute {
                        slug
                    }
                    values {
                        id
                        slug
                        name
                    }
                }
            }
            errors {
                field
                code
                message
            }
        }
    }
"""


def test_update_page_change_attribute_values_ordering(
    staff_api_client,
    permission_manage_pages,
    page,
    product_list,
    page_type_product_reference_attribute,
):
    # given
    page_type = page.page_type
    page_type.page_attributes.set([page_type_product_reference_attribute])

    page_id = graphene.Node.to_global_id("Page", page.pk)

    attribute_id = graphene.Node.to_global_id(
        "Attribute", page_type_product_reference_attribute.pk
    )

    attr_value_1 = AttributeValue.objects.create(
        attribute=page_type_product_reference_attribute,
        name=product_list[0].name,
        slug=f"{page.pk}_{product_list[0].pk}",
        reference_product=product_list[0],
    )
    attr_value_2 = AttributeValue.objects.create(
        attribute=page_type_product_reference_attribute,
        name=product_list[1].name,
        slug=f"{page.pk}_{product_list[1].pk}",
        reference_product=product_list[1],
    )
    attr_value_3 = AttributeValue.objects.create(
        attribute=page_type_product_reference_attribute,
        name=product_list[2].name,
        slug=f"{page.pk}_{product_list[2].pk}",
        reference_product=product_list[2],
    )

    associate_attribute_values_to_instance(
        page,
        page_type_product_reference_attribute,
        attr_value_3,
        attr_value_2,
        attr_value_1,
    )

    assert list(
        page.attributes.first().pagevalueassignment.values_list("value_id", flat=True)
    ) == [attr_value_3.pk, attr_value_2.pk, attr_value_1.pk]

    new_ref_order = [product_list[1], product_list[0], product_list[2]]
    variables = {
        "id": page_id,
        "input": {
            "attributes": [
                {
                    "id": attribute_id,
                    "references": [
                        graphene.Node.to_global_id("Product", ref.pk)
                        for ref in new_ref_order
                    ],
                }
            ]
        },
    }

    # when
    response = staff_api_client.post_graphql(
        UPDATE_PAGE_ATTRIBUTES_MUTATION,
        variables,
        permissions=[permission_manage_pages],
    )

    # then
    content = get_graphql_content(response)
    data = content["data"]["pageUpdate"]
    assert data["errors"] == []

    attributes = data["page"]["attributes"]

    assert len(attributes) == 1
    values = attributes[0]["values"]
    assert len(values) == 3
    assert [value["id"] for value in values] == [
        graphene.Node.to_global_id("AttributeValue", val.pk)
        for val in [attr_value_2, attr_value_1, attr_value_3]
    ]
    page.refresh_from_db()
    assert list(
        page.attributes.first().pagevalueassignment.values_list("value_id", flat=True)
    ) == [attr_value_2.pk, attr_value_1.pk, attr_value_3.pk]


def test_paginate_pages(user_api_client, page, page_type):
    page.is_published = True
    data_02 = {
        "slug": "test02-url",
        "title": "Test page",
        "content": dummy_editorjs("Content for page 1"),
        "is_published": True,
        "page_type": page_type,
    }
    data_03 = {
        "slug": "test03-url",
        "title": "Test page",
        "content": dummy_editorjs("Content for page 1"),
        "is_published": True,
        "page_type": page_type,
    }

    Page.objects.create(**data_02)
    Page.objects.create(**data_03)
    query = """
        query PagesQuery {
            pages(first: 2) {
                edges {
                    node {
                        id
                        title
                    }
                }
            }
        }
        """
    response = user_api_client.post_graphql(query)
    content = get_graphql_content(response)
    pages_data = content["data"]["pages"]
    assert len(pages_data["edges"]) == 2
