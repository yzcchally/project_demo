package com.example.demo

import org.junit.Assert.assertEquals
import org.junit.Test

class RouteHistoryAvailabilityTest {
    @Test
    fun workingRouteOffersPauseBeforeOpening() {
        for (state in listOf("planning_transit", "reading_transit", "validating_transit", "viewing_transit")) {
            assertEquals(RouteHistoryAvailability.PauseToOpen, RouteHistoryAvailability.forState(state, canControl = true))
            assertEquals(RouteHistoryAvailability.Pausing, RouteHistoryAvailability.forState(state, canControl = false, pauseRequested = true))
        }
    }

    @Test
    fun cachedPauseRequiresAConnectionButCompletedRouteStaysAvailable() {
        assertEquals(RouteHistoryAvailability.Connecting, RouteHistoryAvailability.forState("paused", canControl = false))
        assertEquals(RouteHistoryAvailability.Open, RouteHistoryAvailability.forState("paused", canControl = true))
        assertEquals(RouteHistoryAvailability.Open, RouteHistoryAvailability.forState("awaiting_place_choice", canControl = true))
        assertEquals(RouteHistoryAvailability.Open, RouteHistoryAvailability.forState("completed", canControl = false))
    }
}
