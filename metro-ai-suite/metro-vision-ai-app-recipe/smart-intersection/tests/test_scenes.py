# SPDX-FileCopyrightText: (C) 2025 Intel Corporation
# SPDX-License-Identifier: LicenseRef-Intel-Edge-Software
# This file is licensed under the Limited Edge Software Distribution License Agreement.

import pytest
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from tests.utils.ui_utils import waiter, driver
from .conftest import (
  SCENESCAPE_URL,
  SCENESCAPE_REMOTE_URL,
  SCENESCAPE_USERNAME,
  SCENESCAPE_PASSWORD,
)


def verify_intersection_demo_availability(waiter, url):
  """Helper function to verify the Intersection-Demo scene."""
  # Perform login using Waiter class object
  waiter.perform_login(
    url,
    By.ID, "username",
    By.ID, "password",
    By.ID, "login-submit",
    SCENESCAPE_USERNAME, SCENESCAPE_PASSWORD
  )

  # Find the link element that contains the image with alt text "Intersection-Demo"
  link_element = waiter.wait_and_assert(
    EC.presence_of_element_located((By.XPATH, "//a[img[@alt='Intersection-Demo']]")),
    error_message="Link containing image with alt text 'Intersection-Demo' is not present on the page"
  )
  link_element.click()

  # Verify that the scene name element is present and has the correct text
  scene_name_element = waiter.wait_and_assert(
    EC.presence_of_element_located((By.ID, "scene_name")),
    error_message="Scene name element is not present or text does not match 'Intersection-Demo'"
  )
  assert scene_name_element.text == "Intersection-Demo", (
    "Scene name text does not match 'Intersection-Demo'"
  )

def create_and_verify_scene(waiter, name_of_new_scene):
  """Helper function to create and verify a new scene."""
  # Perform login using Waiter class object
  waiter.perform_login(
    SCENESCAPE_URL,
    By.ID, "username",
    By.ID, "password",
    By.ID, "login-submit",
    SCENESCAPE_USERNAME, SCENESCAPE_PASSWORD
  )

  # Find the link element with id 'new_scene' and click it
  new_scene_link = waiter.wait_and_assert(
    EC.presence_of_element_located((By.ID, "new_scene")),
    error_message="Link with id 'new_scene' is not present on the page"
  )
  new_scene_link.click()

  # Verify that the 'Save New Scene' button is present
  save_button = waiter.wait_and_assert(
    EC.presence_of_element_located((By.ID, "save")),
    error_message="Save New Scene button is not present on the page"
  )

  # Fill in the scene name and pixel per meter inputs
  scene_name_input = waiter.driver.find_element(By.ID, "id_name")
  pixel_per_meter_input = waiter.driver.find_element(By.ID, "id_scale")

  scene_name_input.send_keys(name_of_new_scene)
  pixel_per_meter_input.send_keys("100")

  # Click the save button
  save_button.click()

  # Verify that the new scene card is present
  waiter.wait_and_assert(
    EC.presence_of_element_located((By.XPATH, f"//div[@class='card' and @name='{name_of_new_scene}']")),
    error_message=f"Scene card with name '{name_of_new_scene}' is not present on the page"
  )

def interact_with_help_section(waiter, tab_id, help_button_id, modal_id):
  """Interact with a help section by clicking the tab, help button, and closing the modal."""
  # Find and click the tab
  tab = waiter.wait_and_assert(
    EC.presence_of_element_located((By.ID, tab_id)),
    error_message=f"{tab_id} is not present on the page"
  )
  tab.click()

  # Find and click the help button
  help_button = waiter.wait_and_assert(
    EC.element_to_be_clickable((By.ID, help_button_id)),
    error_message=f"{help_button_id} is not present on the page"
  )
  help_button.click()

  # Wait for the modal to be visible
  waiter.wait_and_assert(
    EC.visibility_of_element_located((By.ID, modal_id)),
    error_message=f"{modal_id} is not visible"
  )

  # Find and click the "Close" button in the modal
  close_button = waiter.wait_and_assert(
    EC.element_to_be_clickable((By.XPATH, f"//div[@id='{modal_id}']//button[@type='button' and @class='btn btn-secondary' and @data-dismiss='modal']")),
    error_message=f"Close button is not clickable in the {modal_id}"
  )
  close_button.click()

@pytest.mark.zephyr_id("NEX-T9391")
def test_scene_help(waiter):
  """Test that the scene help is available."""
  # Perform login using Waiter class object
  waiter.perform_login(
    SCENESCAPE_URL,
    By.ID, "username",
    By.ID, "password",
    By.ID, "login-submit",
    SCENESCAPE_USERNAME, SCENESCAPE_PASSWORD
  )

  # Find the link element that contains the image with alt text "Intersection-Demo"
  link_element = waiter.wait_and_assert(
    EC.presence_of_element_located((By.XPATH, "//a[img[@alt='Intersection-Demo']]")),
    error_message="Link containing image with alt text 'Intersection-Demo' is not present on the page"
  )
  link_element.click()

  # Interact with each help section
  interact_with_help_section(waiter, "cameras-tab", "camera-help", "cameraHelpModal")
  interact_with_help_section(waiter, "sensors-tab", "sensor-help", "sensorHelpModal")
  interact_with_help_section(waiter, "regions-tab", "roi-help", "roiHelpModal")
  interact_with_help_section(waiter, "tripwires-tab", "tripwire-help", "tripwireHelpModal")
  interact_with_help_section(waiter, "children-tab", "children-help", "childrenHelpModal")


@pytest.mark.zephyr_id("NEX-T9370")
def test_intersection_demo_availability(waiter):
  """Test that Intersection-Demo is visible after login."""
  # Perform login using Waiter class object
  verify_intersection_demo_availability(waiter, SCENESCAPE_URL)

@pytest.mark.zephyr_id("NEX-T9372")
def test_remote_intersection_demo_availability(waiter):
  """Test that Intersection-Demo is visible after login via remote."""
  if not SCENESCAPE_REMOTE_URL:
    pytest.skip("SCENESCAPE_REMOTE_URL is not set")

  verify_intersection_demo_availability(waiter, SCENESCAPE_REMOTE_URL)



@pytest.mark.zephyr_id("NEX-T9380")
def test_add_scene(waiter):
  """Test that the admin can add a new scene."""
  name_of_new_scene = "new_scene_NEX-T9380"
  create_and_verify_scene(waiter, name_of_new_scene)

@pytest.mark.zephyr_id("NEX-T9381")
def test_delete_scene(waiter):
  """Test that the admin can add and delete a new scene."""
  name_of_new_scene = "new_scene_NEX-T9381"
  create_and_verify_scene(waiter, name_of_new_scene)

  # Find the 'Delete' link within the scene card and click it
  delete_link = waiter.wait_and_assert(
    EC.presence_of_element_located((By.XPATH, f"//div[@class='card' and @name='{name_of_new_scene}']//a[@name='Delete']")),
    error_message=f"Delete link for scene '{name_of_new_scene}' is not present on the page"
  )
  delete_link.click()

  # Verify that the confirmation button is present and click it
  confirm_delete_button = waiter.wait_and_assert(
    EC.presence_of_element_located((By.XPATH, "//input[@class='btn btn-primary' and @value='Yes, Delete the Scene!']")),
    error_message="Confirmation button 'Yes, Delete the Scene!' is not present on the page"
  )
  confirm_delete_button.click()

  # Verify that the scene card is no longer present
  waiter.wait_and_assert(
    EC.invisibility_of_element_located((By.XPATH, f"//div[@class='card' and @name='{name_of_new_scene}']")),
    error_message=f"Scene card with name '{name_of_new_scene}' is still present on the page after deletion"
  )
